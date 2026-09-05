/* Local guided workspace. Queue revisions and readiness belong to the server. */
"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const state = {
  csrf: null, initialized: false, queue: null, data: null, queues: [], lanes: [],
  screen: "batches", group: 0, busy: false, loading: false, queueRequest: 0,
  queueAbort: null, listRequest: 0, job: null, polling: false, cursor: 0,
  pollFailed: false, jobOrigin: null, edit: null, approval: null, dialogFocus: null,
};
const storageKey = "layer8-guided-batch";
const terminalStates = new Set(["done", "completed", "succeeded", "failed", "cancelled", "interrupted"]);
const successStates = new Set(["done", "completed", "succeeded"]);
const jobLabels = {
  generate: "Creating batch", generation: "Creating batch", prepare: "Preparing previews",
  reschedule: "Planning schedule", "stage-approval": "Staging approval PR", approve: "Checking and merging reviewed PR",
  "refresh-delivery": "Refreshing delivery evidence",
};
function canCancel(job) {
  return !!job && (job.cancellable === true ||
    (job.cancellable !== false && ["generate", "generation", "prepare"].includes(job.kind)));
}

function esc(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function tell(message) {
  $("#notice").textContent = message;
  $("#notice").hidden = !message;
}
function showError(error) {
  const message = error.message || String(error);
  const dialog = $("dialog[open]");
  if (dialog) {
    let notice = dialog.querySelector(".dialog-error");
    if (!notice) {
      notice = document.createElement("p");
      notice.className = "notice error dialog-error";
      notice.setAttribute("role", "alert");
      dialog.querySelector(".section-head").after(notice);
    }
    notice.textContent = message;
  }
  $("#error-text").textContent = message;
  $("#error").hidden = false;
}
function remember(name) {
  try {
    if (name) localStorage.setItem(storageKey, name);
    else localStorage.removeItem(storageKey);
  } catch (error) {
    tell(`Browser storage is unavailable; selection cannot survive reload. ${error.message}`);
  }
}
function recalledQueue() {
  try { return localStorage.getItem(storageKey); }
  catch (error) { showError(new Error(`Could not restore your selected batch: ${error.message}`)); return null; }
}
function endpoint(name, action = "") {
  return `/api/queue/${encodeURIComponent(name)}${action ? `/${action}` : ""}`;
}
async function api(path, options = {}) {
  const { responseType, ...requestOptions } = options;
  const response = await fetch(path, { cache: "no-store", ...requestOptions });
  if (response.ok && responseType === "blob") return response.blob();
  const body = (response.headers.get("Content-Type") || "").includes("json")
    ? await response.json() : await response.text();
  if (!response.ok) {
    const error = new Error(body?.error || body?.detail || (typeof body === "string" && body) || `Request failed (HTTP ${response.status}).`);
    error.status = response.status;
    throw error;
  }
  return body;
}
function post(path, body = {}, revision = null, headers = {}) {
  if (!state.csrf) throw new Error("The local session is not connected. Reload before making changes.");
  const requestHeaders = { "X-Layer8-CSRF": state.csrf, ...headers };
  if (revision !== null) requestHeaders["If-Match"] = String(revision);
  if (!(body instanceof Blob)) {
    requestHeaders["Content-Type"] = "application/json";
    body = JSON.stringify(body);
  }
  return api(path, { method: "POST", headers: requestHeaders, body });
}
function safeURL(value) {
  if (!value) return "";
  try {
    const url = new URL(value, window.location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch { return ""; }
}
function mediaURL(value) {
  const url = safeURL(value);
  if (!url) return "";
  const result = new URL(url);
  result.searchParams.set("revision", String(state.data?.revision || ""));
  return result.href;
}
function externalLink(url, label) {
  const safe = safeURL(url);
  return safe ? `<a href="${esc(safe)}" target="_blank" rel="noopener noreferrer">${esc(label)}<span class="sr-only"> (new tab)</span></a>` : "";
}
function easternTime(value) {
  if (!value) return "No schedule set - choose an Eastern date.";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return `Invalid schedule: ${value}`;
  return `${new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", weekday: "short", month: "short", day: "numeric",
    year: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "long",
  }).format(date)} (America/New_York)`;
}
function easternDate() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date());
  const part = (type) => parts.find((item) => item.type === type).value;
  return `${part("year")}-${part("month")}-${part("day")}`;
}
function issueText(issue) {
  if (typeof issue === "string") return issue;
  return [issue.post_id || issue.image_id, issue.detail || issue.message || issue.reason || issue.code || "Unspecified issue"].filter(Boolean).join(": ");
}
function sourceMissing(image) {
  return !image.original && ["pending", "missing", "failed", "invalid"].includes(image.status);
}
function isLocked() {
  return !state.initialized || state.busy || state.loading || !!state.job;
}
function batchSnapshot() {
  if (!state.queue || !state.data || !state.data.revision) throw new Error("Load a current batch revision before making changes.");
  return { queue: state.queue, revision: state.data.revision };
}
async function handleError(error) {
  if (error.name === "AbortError") return;
  showError(error);
  if ([409, 412].includes(error.status)) {
    tell("Your action was not retried. Refreshing the batch; review the latest revision before trying again.");
    try { await refreshQueue(); } catch (refreshError) { showError(refreshError); }
    try {
      const session = await api("/api/session");
      if (session.active_job) attachJob(session.active_job);
    } catch (sessionError) { showError(sessionError); }
  }
}
async function mutate(action, body = {}, headers = {}) {
  if (isLocked()) throw new Error("Wait for the current operation to finish before changing this batch.");
  const snapshot = batchSnapshot();
  state.busy = true;
  renderControls();
  try {
    const result = await post(endpoint(snapshot.queue, action), body, snapshot.revision, headers);
    await refreshQueue();
    await loadQueues();
    return result;
  } finally { state.busy = false; renderControls(); }
}

function showScreen(screen, focus = true) {
  if (screen !== "batches" && !state.data) return;
  state.screen = screen;
  $$(".screen").forEach((element) => { element.hidden = element.id !== `screen-${screen}`; });
  $$("[data-screen]").forEach((button) => {
    if (button.dataset.screen === screen) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
  });
  renderControls();
  if (focus) $(`#heading-${screen}`).focus();
}
function preferredScreen(data) {
  const next = typeof data.next_action === "object" ? data.next_action?.action : data.next_action;
  const known = { images: "images", "get-images": "images", "get_images": "images", prepare: "prepare", "prepare_previews": "prepare", review: "review", approve: "review", reschedule: "review", delivery: "delivery" };
  if (known[next]) return known[next];
  if (data.approval?.state?.toLowerCase() === "merged") return "delivery";
  if ((data.images || []).some(sourceMissing) || data.staged?.length) return "images";
  return data.readiness?.media_ready ? "review" : "prepare";
}
async function loadQueues() {
  const request = ++state.listRequest;
  const result = await api("/api/queues");
  if (request !== state.listRequest) return;
  state.queues = result.queues || [];
  $("#queue-list").innerHTML = state.queues.length ? state.queues.map((queue) => {
    const readiness = queue.readiness;
    const summary = queue.error ? `Cannot read batch: ${queue.error}` :
      readiness?.ready ? "Ready for review, not yet delivered" :
      readiness?.media_ready && !readiness?.schedule_ready ? "Schedule needs attention" :
      queue.next_action?.label || queue.next_action?.detail || (typeof queue.next_action === "string" ? queue.next_action.replaceAll("_", " ") : "") ||
      `${queue.pending || 0} sources outstanding`;
    return `<button type="button" class="queue-card" data-queue="${esc(queue.name)}" aria-pressed="${queue.name === state.queue}">
      <span><strong>${esc(queue.lane || "Batch")}</strong><span class="meta">${esc(queue.name)} &middot; ${esc(queue.posts ?? 0)} posts</span></span>
      <span class="status ${queue.error ? "bad" : ""}">${esc(summary)}</span></button>`;
  }).join("") : '<p class="muted">No batches yet. Choose a brand and date below to create one.</p>';
  if (!state.queues.length) $("#new-batch").open = true;
  renderControls();
}
async function loadLanes() {
  const result = await api("/api/lanes");
  state.lanes = result.lanes || [];
  $("#gen-lane").innerHTML = state.lanes.map((lane) => `<option value="${esc(lane.lane)}">${esc(lane.label)}</option>`).join("");
  const unavailable = result.copilot && !result.copilot.ok;
  $("#generation-warning").hidden = !unavailable;
  $("#generation-warning").textContent = unavailable ? result.copilot.error || "Copilot CLI is not available. Existing batches remain usable." : "";
  $("#gen-lane").dataset.available = unavailable ? "false" : "true";
  chooseLane();
}
function chooseLane() {
  const lane = state.lanes.find((item) => item.lane === $("#gen-lane").value);
  if (lane) {
    $("#gen-date").value = lane.default_date || easternDate();
    $("#gen-description").textContent = lane.blurb || lane.label;
  }
  renderControls();
}
async function selectQueue(name, navigate = true) {
  state.queue = name;
  state.data = null;
  state.group = 0;
  remember(name);
  showScreen("batches", false);
  renderControls();
  const data = await refreshQueue();
  if (!data || state.queue !== name) return;
  if (navigate) showScreen(preferredScreen(data));
  await loadQueues();
}
async function refreshQueue() {
  if (!state.queue) return null;
  const name = state.queue;
  const request = ++state.queueRequest;
  state.queueAbort?.abort();
  state.queueAbort = new AbortController();
  state.loading = true;
  renderControls();
  try {
    const data = await api(endpoint(name), { signal: state.queueAbort.signal });
    // A slow response from a previously selected batch must never replace this one.
    if (request !== state.queueRequest || state.queue !== name) return null;
    if (state.data && state.data.revision !== data.revision && $("#approve-dialog").open) {
      $("#approve-dialog").close();
      tell("The batch changed. Review the new revision before approving.");
    }
    state.data = data;
    renderBatch();
    if (data.active_job) attachJob(data.active_job);
    return data;
  } catch (error) {
    if (error.name === "AbortError" || request !== state.queueRequest) return null;
    throw error;
  } finally {
    if (request === state.queueRequest) { state.loading = false; renderControls(); }
  }
}
function renderBatch() {
  const data = state.data;
  $("#batch-name").textContent = data.name || state.queue;
  $("#batch-lane").textContent = data.lane || "Selected batch";
  $("#batch-details").innerHTML = `<dt>Queue</dt><dd>${esc(state.queue)}</dd><dt>Current revision</dt><dd>${esc(data.revision)}</dd>`;
  renderReadiness();
  renderPrompts();
  renderStaged();
  $("#prepare-images").innerHTML = (data.images || []).map(imageCard).join("");
  $("#prepare-summary").textContent = data.readiness?.media_ready
    ? "Final media is prepared. This is not approval or delivery."
    : "Inspect the expected scenes below. Missing sources, failed output or stale previews still need attention.";
  renderReview();
  renderApproval();
  renderDelivery();
  const download = $("#download-batch");
  download.hidden = !data.readiness?.media_ready;
  download.href = endpoint(state.queue, "download");
  renderControls();
}
function renderReadiness() {
  const ready = state.data.readiness;
  const box = $("#readiness");
  if (!ready) {
    box.innerHTML = '<div class="notice error">Readiness is unavailable. Approval is disabled until the server provides a current report.</div>';
    return;
  }
  const blockers = ready.blockers || [];
  const warnings = ready.warnings || [];
  const summary = !ready.schedule_ready
    ? '<p class="warning">Schedule needs attention. Expired times are not moved automatically. Choose <button type="button" data-action="reschedule">Reschedule batch</button> and review the new Eastern times.</p>' : "";
  box.innerHTML = `${summary}${blockers.length ? `<div class="notice"><strong class="bad">Needs attention</strong><ul>${blockers.map((issue) => `<li>${esc(issueText(issue))}</li>`).join("")}</ul></div>` : ""}
    ${warnings.length ? `<div class="notice"><strong class="warning">Warnings to review</strong><ul>${warnings.map((issue) => `<li>${esc(issueText(issue))}</li>`).join("")}</ul></div>` : ""}`;
}
function imageCard(image) {
  const url = image.preview || image.original;
  const label = image.headline || image.slide_role || "Image without a headline";
  const status = { ready: "Source imported; preview not prepared", done: "Preview prepared", pending: "Source needed", stale: "Preview needs updating" }[image.status] || image.status;
  return `<article class="image-card">
    ${url ? `<button type="button" class="media-button" data-action="compare" data-image="${esc(image.image_id)}" aria-label="Compare source and final: ${esc(label)}"><img src="${esc(mediaURL(url))}" alt="${esc(label)}" loading="lazy"></button>` : '<p class="muted">No source image yet</p>'}
    <h3>${esc(label)}</h3><p class="meta">${esc(image.aspect)} &middot; ${esc(image.format)}${image.slide_index ? ` &middot; Slide ${esc(image.slide_index)}` : ""}</p>
    <span class="status">${esc(status || "Status unavailable")}</span>
    ${image.warning ? `<p class="warning">${esc(issueText(image.warning))}</p>` : ""}
    <p class="scene"><strong>Expected scene:</strong> ${esc(image.scene || "See the exact scene in the image prompt below.")}</p>
    ${image.subtext ? `<p class="meta">Subtext: ${esc(image.subtext)}</p>` : ""}
    <div class="actions"><button type="button" data-action="compare" data-image="${esc(image.image_id)}">Compare / replace</button><button type="button" data-action="edit-image" data-image="${esc(image.image_id)}">Edit headline</button></div>
    <details><summary>Image details and prompt</summary><p>${esc(image.filename)} &middot; ${esc(image.size)}</p><pre class="promptbox" tabindex="0">${esc(image.prompt || "")}</pre></details>
  </article>`;
}
function renderPrompts() {
  const groups = state.data.prompt_groups || [];
  const images = state.data.images || [];
  const hasPending = (group) => group?.image_ids?.some((id) => images.some((image) => image.image_id === id && sourceMissing(image)));
  if (!groups[state.group] || !hasPending(groups[state.group])) {
    const outstanding = groups.findIndex(hasPending);
    state.group = outstanding >= 0 ? outstanding : 0;
  }
  $("#prompt-group").innerHTML = groups.map((group, index) => `<option value="${index}">Group ${index + 1} - ${group.image_ids.length} images${hasPending(group) ? " outstanding" : " imported"}</option>`).join("");
  $("#prompt-group").value = String(state.group);
  renderGroup();
}
function renderGroup() {
  const groups = state.data?.prompt_groups || [];
  const group = groups[state.group];
  $("#group-prompt").textContent = group?.prompt || "No outstanding image prompts.";
  const selected = (group?.image_ids || []).map((id) => state.data.images.find((image) => image.image_id === id)).filter(Boolean);
  $("#group-images").innerHTML = selected.map(imageCard).join("");
  $("#group-progress").textContent = group
    ? `Group ${state.group + 1} of ${groups.length}. ${selected.filter((image) => !sourceMissing(image)).length} of ${selected.length} sources imported. Groups advance after imports, not after copying.`
    : "All prompt groups are complete, or this batch does not need manual images.";
  renderControls();
}
async function copyGroup() {
  const prompt = state.data?.prompt_groups?.[state.group]?.prompt;
  if (!prompt) throw new Error("No prompt is available for this group.");
  try {
    await navigator.clipboard.writeText(prompt);
    tell("Prompt group copied. Paste it into ChatGPT, generate the images yourself, then return with the files.");
  } catch (error) {
    $("#group-prompt").closest("details").open = true;
    const range = document.createRange();
    range.selectNodeContents($("#group-prompt"));
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
    $("#group-prompt").focus();
    showError(new Error(`Automatic copying is unavailable. The prompt is selected; copy it manually. ${error.message}`));
  }
}
function renderStaged() {
  const staged = state.data.staged || [];
  $("#reconcile").hidden = !staged.length;
  const open = (state.data.images || []).filter(sourceMissing);
  $("#staged-list").innerHTML = staged.map((item, index) => `<article class="staged">
    <img src="${esc(mediaURL(item.url))}" alt="Unassigned image: ${esc(item.file)}" loading="lazy">
    <div><h4>${esc(item.file)}</h4><p class="warning">${esc(item.reason || "Needs a matching image slot")}</p><p class="meta">${esc(item.size)}</p>
    <form data-assign-file="${esc(item.file)}"><label for="assignment-${index}">Expected image<select id="assignment-${index}" name="image_id" required>
    <option value="">Choose a matching scene...</option>${open.map((image) => `<option value="${esc(image.image_id)}">${esc(image.headline || image.image_id)} - ${esc(image.aspect)}${image.slide_index ? ` - slide ${esc(image.slide_index)}` : ""}</option>`).join("")}
    </select></label><button type="submit">Assign image</button></form></div></article>`).join("");
}
function fileHeaders(file) {
  const ext = file.name.split(".").pop().toLowerCase();
  const types = { zip: "application/zip", png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp" };
  if (!types[ext]) throw new Error(`${file.name}: choose ZIP, PNG, JPEG or WebP files.`);
  return { "X-Filename": encodeURIComponent(file.name), "Content-Type": types[ext] };
}
async function uploadFiles(files) {
  if (!files.length) return;
  if (isLocked()) throw new Error("Wait for the current operation before uploading.");
  const snapshot = batchSnapshot();
  files.forEach(fileHeaders);
  state.busy = true;
  renderControls();
  const report = $("#upload-report");
  report.hidden = false;
  const messages = [];
  try {
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      report.textContent = `Importing ${index + 1} of ${files.length}: ${file.name}`;
      const result = await post(endpoint(snapshot.queue, "upload"), file, snapshot.revision, fileHeaders(file));
      messages.push(`${file.name}: ${result.matched?.length || 0} matched; ${result.staged?.length || 0} awaiting assignment.`);
      if (result.skipped?.length) messages.push(`Skipped: ${result.skipped.map(issueText).join("; ")}`);
      const refreshed = await refreshQueue();
      if (!refreshed || state.queue !== snapshot.queue) throw new Error("The selected batch changed. Remaining files were not uploaded.");
      snapshot.revision = refreshed.revision;
    }
    report.textContent = messages.join("\n");
    tell("Import finished. Check each match before preparing previews.");
    await loadQueues();
  } catch (error) {
    report.textContent = `${messages.join("\n")}\nImport stopped: ${error.message}. Earlier accepted files were kept; remaining files were not retried.`;
    throw error;
  } finally { state.busy = false; renderControls(); }
}

function orderedPreviews(postData) {
  const seen = new Set();
  return (postData.previews || []).filter((media) => {
    const identity = media.url || media.name;
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });
}
function imageForMedia(media) {
  return (state.data.images || []).find((image) => image.filename === media.name || image.preview === media.url);
}
function renderReview() {
  $("#review-list").innerHTML = (state.data.posts || []).map((postData) => {
    const previews = orderedPreviews(postData);
    const media = previews.map((item, index) => {
      const image = imageForMedia(item);
      const label = postData.format === "carousel" ? `Slide ${index + 1} of ${previews.length}` : item.is_video ? "Video" : "Image / cover";
      return `<figure>${item.is_video
        ? `<video src="${esc(mediaURL(item.url))}" controls playsinline preload="metadata" aria-label="${esc(postData.id)} video preview"></video><button type="button" data-action="view-media" data-post="${esc(postData.id)}" data-media="${index}">Full-size video</button>`
        : `<button type="button" class="media-button" data-action="${image ? "compare" : "view-media"}" data-image="${esc(image?.image_id)}" data-post="${esc(postData.id)}" data-media="${index}" aria-label="Inspect ${esc(label)}: ${esc(image?.headline || postData.id)}"><img src="${esc(mediaURL(item.url))}" alt="${esc(image?.headline || `${postData.id} ${label}`)}" loading="lazy"></button>`}
        <figcaption>${esc(label)}${image?.headline ? ` - ${esc(image.headline)}` : ""}</figcaption></figure>`;
    }).join("");
    return `<article class="post" data-post-id="${esc(postData.id)}"><div class="post-head"><h3>${esc(postData.platform)} &middot; ${esc(postData.format)}</h3><button type="button" data-action="edit-post" data-post="${esc(postData.id)}">Edit post</button></div>
      <p class="meta">${esc(postData.account || state.data.lane)}${postData.category ? ` / ${esc(postData.category)}` : ""}</p>
      <time datetime="${esc(postData.schedule_time)}">${esc(easternTime(postData.schedule_time))}</time>
      <div class="media">${media || '<p class="bad">No final media available. Prepare previews before approval.</p>'}</div>
      <div class="post-text">
        ${postData.platform === "youtube" || postData.youtube_title ? `<h4>YouTube title</h4><p>${esc(postData.youtube_title || "No title set")}</p>` : ""}
        <h4>Caption</h4><p class="caption">${esc(postData.text || "No caption set")}</p>
        <h4>Hashtags</h4><p class="tags">${esc((postData.hashtags || []).join(" ") || "None")}</p>
        <h4>First comment</h4><p class="first-comment">${esc(postData.first_comment || "None")}</p>
      </div><details><summary>Post details</summary><p>${esc(postData.id)}</p><button type="button" class="danger" data-action="delete-post" data-post="${esc(postData.id)}">Remove this post</button></details></article>`;
  }).join("") || '<p class="muted">No posts in this batch.</p>';
}
function approvalIsCurrent() {
  const approval = state.data?.approval;
  return !!(approval?.pr_number && approval.head_sha && approval.revision === state.data.revision &&
    !["merged", "closed", "stale"].includes(String(approval.state).toLowerCase()));
}
function approvalIdentity(approval) {
  return `<dl><dt>Approval PR</dt><dd>${externalLink(approval.pr_url, `PR #${approval.pr_number}`) || `PR #${esc(approval.pr_number)}`}</dd>
    <dt>Content revision</dt><dd>${esc(approval.revision)}</dd><dt>Exact PR head</dt><dd>${esc(approval.head_sha)}</dd>
    <dt>PR state (not provider delivery)</dt><dd>${esc(approval.state || "Awaiting approval")}</dd></dl>
    ${externalLink(approval.workflow_url, "View publishing workflow")}`;
}
function renderApproval() {
  const approval = state.data.approval;
  $("#approval-panel").innerHTML = `<h3>Approval is a separate decision</h3>
    <p>First stage a PR containing this exact batch. Then explicitly approve its displayed revision and head. Required checks and branch protections still apply.</p>
    ${approval ? approvalIdentity(approval) : '<p class="muted">No approval PR has been staged for this batch.</p>'}
    ${approval && !approvalIsCurrent() && String(approval.state).toLowerCase() !== "merged" ? '<p class="warning">This PR does not represent the current reviewable revision. Stage the current revision again.</p>' : ""}`;
}
function renderDelivery() {
  const approval = state.data.approval;
  $("#delivery-approval").innerHTML = approval ? approvalIdentity(approval) : '<p class="muted">No approval PR yet. Nothing here proves provider delivery.</p>';
  const workflow = state.data.workflow;
  const observedAt = state.data.observed_at || workflow?.observed_at;
  const failedConclusions = new Set(["failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"]);
  const failed = workflow && failedConclusions.has(workflow.conclusion);
  const workflowMessage = !workflow ? "No publishing workflow status observed. Refresh delivery to check GitHub."
    : failed ? `Publishing workflow needs attention: ${workflow.conclusion.replaceAll("_", " ")}. Earlier posts may still have been submitted; inspect receipts before recovery.`
    : workflow.conclusion === "success" ? "Publishing workflow completed successfully. Inspect each receipt; workflow success alone is not proof of publication."
    : `Publishing workflow: ${(workflow.status || "unknown").replaceAll("_", " ")}${workflow.conclusion ? ` (${workflow.conclusion.replaceAll("_", " ")})` : ""}. This does not establish provider delivery.`;
  $("#delivery-workflow").innerHTML = `<div class="notice">
    <strong class="${failed ? "bad" : ""}">${esc(workflowMessage)}</strong>
    <p class="meta">${observedAt ? `Evidence observed ${esc(easternTime(observedAt))}.` : "No delivery observation time recorded."} Cached evidence, not a live provider check.</p>
    ${externalLink(workflow?.url, "View publishing workflow")}</div>`;
  const labels = { awaiting_approval: "Awaiting approval", not_submitted: "Not submitted",
    submission_pending: "Awaiting submission evidence", prepared: "Prepared only", merged: "PR merged only",
    submitted: "Submitted; awaiting provider confirmation", accepted: "Provider accepted", queued: "Provider queued",
    scheduled: "Provider scheduled", published: "Published", private: "Private upload", inbox: "Inbox-only upload",
    inbox_only: "Inbox-only upload", skipped: "Skipped", failed: "Failed", unknown: "Unknown; reconcile before retrying",
    revision_changed: "Receipt belongs to a different revision" };
  const receipts = state.data.delivery || [];
  $("#delivery-list").innerHTML = receipts.length ? receipts.map((entry) => {
    const deliveryState = entry.state || entry.delivery_status;
    const providerId = entry.provider_id || entry.postiz_post_id || entry.ghl_post_id;
    return `<article class="delivery-item">
      <h3>${esc(entry.id || entry.post_id || "Batch delivery")}</h3><p class="eyebrow">${entry.source === "none" ? "No receipt observed" : "Observed receipt status"}</p><span class="status">${esc(labels[deliveryState] || deliveryState || "No delivery state")}</span>
      <p>${esc(entry.detail || entry.skip_reason || "No additional provider evidence.")}</p>
      ${entry.delivery_mode ? `<p>Delivery mode: ${esc(entry.delivery_mode)}</p>` : ""}
      ${entry.visibility ? `<p class="${entry.visibility === "private" ? "warning" : ""}">Visibility: ${esc(entry.visibility)}${entry.visibility === "private" ? " - not a public post." : ""}</p>` : ""}
      <div class="actions">${externalLink(entry.url, "View receipt")}${externalLink(entry.workflow_url, "View workflow")}${externalLink(entry.pr_url, "View PR")}</div>
      ${providerId ? `<details><summary>Provider details</summary><p>${esc(providerId)}</p></details>` : ""}</article>`;
  }).join("")
    : '<div class="notice">No provider receipts recorded. Prepared media or a merged PR is not proof that posts are scheduled.</div>';
}
function primaryAction() {
  if (!state.initialized) return { label: "Connecting...", note: "Connect to the local server before changing content.", disabled: true };
  if (state.job) return { label: "Work in progress", note: state.pollFailed ? "Reconnect to the job above. Mutations stay locked until its state is known." : "Progress is saved. You may inspect other batches while this operation runs.", disabled: true };
  if (state.busy || state.loading) return { label: "Please wait...", note: "Finishing the current request.", disabled: true };
  if (state.screen === "batches") {
    if ($("#new-batch").open) {
      const lane = state.lanes.find((item) => item.lane === $("#gen-lane").value);
      return { label: "Create batch", note: "Uses the selected brand and Eastern date. This only generates a draft.", action: "generate",
        disabled: !lane || lane.prompt_ok === false || $("#gen-lane").dataset.available !== "true" || !$("#gen-date").value };
    }
    return { label: state.data ? "Resume selected batch" : "Create a new batch", note: state.data ? state.queue : "Select an existing batch above or choose a new brand and date.",
      action: state.data ? "resume" : "new" };
  }
  if (!state.data) return { label: "Select a batch", note: "Choose a batch first.", action: "batches" };
  const ready = state.data.readiness;
  if (state.screen === "images") {
    if (state.data.staged?.length) return { label: "Match imported images", note: "Assign the images awaiting a decision before continuing.", action: "match" };
    if ((state.data.images || []).some(sourceMissing)) return { label: "Copy this prompt group", note: "Paste into ChatGPT yourself. Bring back a ZIP or individual files.", action: "copy",
      disabled: !state.data.prompt_groups?.[state.group]?.prompt };
    return { label: "Continue to previews", note: "Sources are imported. Preparation and final review still come next.", action: "prepare-screen" };
  }
  if (state.screen === "prepare") {
    if ((state.data.images || []).some(sourceMissing) || state.data.staged?.length) return { label: "Finish image imports", note: "Some sources still need importing or matching.", action: "images" };
    return ready?.media_ready ? { label: "Review prepared batch", note: "All final media is prepared, not approved or scheduled.", action: "review" }
      : { label: "Prepare previews", note: "Finish changed images and any required videos in one operation.", action: "prepare" };
  }
  if (state.screen === "review") {
    if (String(state.data.approval?.state).toLowerCase() === "merged") return { label: "View delivery evidence", note: "This PR is merged. Provider receipts establish what happened next.", action: "delivery" };
    if (!ready?.media_ready) return { label: "Prepare missing previews", note: "Final media must be complete before staging approval.", action: "prepare-screen" };
    if (!ready.schedule_ready) return { label: "Choose a fresh schedule", note: "Expired or invalid times block approval. Dates never roll forward automatically.", action: "reschedule" };
    if (!ready.ready) return { label: "Resolve blockers before approval", note: "See the current readiness report above. Warnings are separate from blockers.", disabled: true };
    return approvalIsCurrent()
      ? { label: "Approve displayed revision", note: "Explicit approval merges only the displayed PR head if checks permit.", action: "approve" }
      : { label: "Stage approval PR", note: "Prepare the exact candidate for approval. This does not merge or publish it.", action: "stage-approval" };
  }
  return { label: "Refresh delivery status", note: "Fetch the latest GitHub workflow and receipt evidence. This never submits or retries posts.", action: "refresh-delivery" };
}
function renderControls() {
  const locked = isLocked();
  $$("[data-screen]").forEach((button) => { button.disabled = (button.dataset.screen !== "batches" && !state.data) || state.busy; });
  $$("[data-queue]").forEach((button) => { button.disabled = state.busy; });
  $("#gen-lane").disabled = locked || !state.lanes.length;
  $("#gen-date").disabled = locked;
  $("#image-files").disabled = locked || !state.data;
  $("#refresh-batch").disabled = state.busy;
  $("#refresh-batch").textContent = state.screen === "delivery" ? "Reload cached batch" : "Refresh batch";
  $("#refresh-queues").disabled = state.busy;
  $("#delete-batch").disabled = locked || !state.data;
  $("#batch-heading").hidden = !state.data;
  $("#batch-tools").hidden = !state.data;
  $("#readiness").hidden = !state.data || state.screen === "batches";
  $$("[data-action='edit-post'], [data-action='edit-image'], [data-action='delete-post'], [data-action='reschedule'], [data-action='undo-image'], [data-assign-file] button, [data-assign-file] select, [data-replace-image], [data-restore], #edit-form button, #reschedule-form button").forEach((element) => { element.disabled = locked; });
  const next = primaryAction();
  $("#next-action").textContent = next.label;
  $("#next-action").disabled = !!next.disabled;
  $("#next-action").dataset.action = next.action || "";
  $("#next-hint").textContent = next.note;
  $("#approve-submit").disabled = locked || !$("#approve-confirm").checked || !approvalIsCurrent();
}

function openDialog(id) {
  const current = $("dialog[open]");
  if (current) current.close();
  state.dialogFocus = document.activeElement;
  const dialog = $(id);
  dialog.querySelector(".dialog-error")?.remove();
  dialog.showModal();
  dialog.querySelector("button, input, textarea, select")?.focus();
}
function showComparison(imageId) {
  const image = state.data.images.find((item) => item.image_id === imageId);
  if (!image) throw new Error("This image is no longer in the selected batch.");
  $("#media-title").textContent = image.headline || image.image_id;
  $("#media-description").textContent = image.scene || "Compare the original source with the final cropped, typeset output. Open either file at its full resolution.";
  const finished = image.final || (image.status === "done" || image.status === "stale" ? image.preview : null);
  const original = image.original || (image.status === "ready" ? image.preview : null);
  $("#media-comparison").innerHTML = [
    { label: "Original source", url: original },
    { label: ["ready", "stale"].includes(image.status) && finished ? "Previous final preview - prepare again" : "Final preview", url: finished },
  ].map((item) => `<figure><figcaption>${item.label}</figcaption>${item.url
    ? `<img src="${esc(mediaURL(item.url))}" alt="${item.label}: ${esc(image.headline || image.image_id)}">${externalLink(mediaURL(item.url), `Open ${item.label.toLowerCase()} at full resolution`)}`
    : '<p class="muted">Not available yet.</p>'}</figure>`).join("");
  $("#media-tools").innerHTML = `<label>Replace this source<input type="file" accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp" data-replace-image="${esc(imageId)}"></label>
    ${image.has_history ? `<button type="button" data-action="undo-image" data-image="${esc(imageId)}">Undo last replacement</button>` : ""}
    <p class="hint">Replacement preserves the prior source. Prepare changed previews and review again afterwards.</p>`;
  openDialog("#media-dialog");
  renderControls();
}
function showFullMedia(postId, index) {
  const postData = state.data.posts.find((item) => item.id === postId);
  const media = orderedPreviews(postData)[index];
  if (!media) throw new Error("This media is no longer available.");
  $("#media-title").textContent = media.is_video ? "Full-size video" : "Full-size image";
  $("#media-description").textContent = postId;
  $("#media-comparison").innerHTML = `<figure>${media.is_video
    ? `<video src="${esc(mediaURL(media.url))}" controls playsinline preload="metadata" aria-label="Full-size video preview"></video>`
    : `<img src="${esc(mediaURL(media.url))}" alt="${esc(media.name)}">`}<figcaption>${externalLink(mediaURL(media.url), "Open at full resolution")}</figcaption></figure>`;
  $("#media-tools").innerHTML = "";
  openDialog("#media-dialog");
}
function editPost(id) {
  const item = state.data.posts.find((postData) => postData.id === id);
  state.edit = { ...batchSnapshot(), kind: "post", id };
  $("#edit-title").textContent = "Edit post";
  $("#edit-fields").innerHTML = `<label>Caption<textarea name="text" rows="7">${esc(item.text)}</textarea></label>
    <label>Hashtags (separated by spaces)<textarea name="hashtags" rows="2">${esc((item.hashtags || []).join(" "))}</textarea></label>
    <label>First comment<textarea name="first_comment" rows="4">${esc(item.first_comment)}</textarea></label>
    ${item.platform === "youtube" || item.youtube_title ? `<label>YouTube title<input name="youtube_title" value="${esc(item.youtube_title)}"></label>` : ""}
    <label>Schedule timestamp (include UTC offset)<input name="schedule_time" value="${esc(item.schedule_time)}" required aria-describedby="schedule-help"></label>
    <p id="schedule-help" class="hint">Current Eastern time: ${esc(easternTime(item.schedule_time))}. Use a timestamp such as 2026-09-06T14:00:00-04:00. The explicit offset avoids ambiguous daylight-saving times. To replan the whole day, use Reschedule batch instead.</p>`;
  openDialog("#edit-dialog");
}
function editImage(id) {
  const image = state.data.images.find((item) => item.image_id === id);
  state.edit = { ...batchSnapshot(), kind: "image", id };
  $("#edit-title").textContent = "Edit image typography";
  $("#edit-fields").innerHTML = `<label>Expected headline<input name="headline" value="${esc(image.headline)}"></label><label>Subtext<textarea name="subtext">${esc(image.subtext)}</textarea></label>`;
  openDialog("#edit-dialog");
}
async function saveEdit(form) {
  const edit = state.edit;
  if (edit.queue !== state.queue || edit.revision !== state.data?.revision) throw new Error("The batch changed while this editor was open. Close it and review the current revision.");
  const fields = Object.fromEntries(new FormData(form));
  if (edit.kind === "post") {
    fields.hashtags = fields.hashtags.trim() ? fields.hashtags.trim().split(/\s+/) : [];
    if (!/(?:Z|[+-]\d{2}:\d{2})$/i.test(fields.schedule_time) || Number.isNaN(Date.parse(fields.schedule_time))) {
      throw new Error("Schedule must be a valid ISO date/time with an explicit offset (for example -04:00 or -05:00).");
    }
    await mutate("edit-post", { post_id: edit.id, changes: fields });
  } else {
    await mutate("edit-image", { image_id: edit.id, ...fields });
  }
  $("#edit-dialog").close();
  tell("Changes saved. Review the updated revision and prepare any stale previews.");
}
function openReschedule() {
  state.edit = { ...batchSnapshot(), kind: "reschedule" };
  $("#reschedule-date").value = easternDate();
  openDialog("#reschedule-dialog");
}
function openApproval() {
  if (isLocked() || !state.data.readiness?.ready || !approvalIsCurrent()) throw new Error("A ready, current staged PR is required before approval.");
  state.approval = { ...state.data.approval, queue: state.queue };
  $("#approve-identity").innerHTML = approvalIdentity(state.approval);
  $("#approve-confirm").checked = false;
  $("#approve-submit").disabled = true;
  openDialog("#approve-dialog");
}
async function approveDisplayed() {
  const approval = state.approval;
  if (!$("#approve-confirm").checked || !approval || approval.queue !== state.queue ||
    approval.revision !== state.data.revision || approval.head_sha !== state.data.approval?.head_sha ||
    approval.pr_number !== state.data.approval?.pr_number || !state.data.readiness?.ready || !approvalIsCurrent()) {
    throw new Error("Approval no longer matches the displayed batch. Review and stage its current revision first.");
  }
  await startJob("approve", { pr_number: approval.pr_number, head_sha: approval.head_sha, revision: approval.revision });
  $("#approve-dialog").close();
}

async function startJob(kind, body = {}) {
  if (isLocked()) throw new Error("Another operation is active. Wait for its result first.");
  const origin = state.queue;
  const snapshot = kind === "generate" ? null : batchSnapshot();
  state.busy = true;
  renderControls();
  try {
    const result = await post(kind === "generate" ? "/api/generate" : endpoint(snapshot.queue, kind), body, snapshot?.revision ?? null);
    const job = result.job || result;
    if (!job.id) throw new Error("The server did not return a job identifier. Refresh before retrying.");
    attachJob({ ...job, status: "running", kind: job.kind || kind }, origin);
  } finally { state.busy = false; renderControls(); }
}
function attachJob(job, origin = state.queue) {
  if (typeof job === "string") job = { id: job };
  if (!job?.id || terminalStates.has(job.status)) return;
  if (state.job?.id === job.id) return;
  if (state.job) {
    showError(new Error("The server reported a different active job. Reconnect progress before starting more work."));
    return;
  }
  state.job = job;
  state.jobOrigin = job.queue || origin;
  state.cursor = 0;
  state.pollFailed = false;
  $("#job-log").textContent = "";
  $("#job-panel").hidden = false;
  $("#job-status").textContent = jobLabels[job.kind] || "Reconnecting to active work...";
  $("#retry-job").hidden = true;
  renderControls();
  void pollJob();
}
async function pollJob() {
  if (state.polling || !state.job) return;
  state.polling = true;
  const id = state.job.id;
  try {
    while (state.job?.id === id) {
      const snap = await api(`/api/jobs/${encodeURIComponent(id)}?since=${state.cursor}`);
      if (state.job?.id !== id) return;
      state.job = { ...state.job, ...snap };
      if (!state.jobOrigin && snap.queue) state.jobOrigin = snap.queue;
      state.cursor = snap.next ?? state.cursor;
      state.pollFailed = false;
      $("#retry-job").hidden = true;
      const lines = snap.lines || [];
      if (lines.length) $("#job-log").textContent += `${lines.join("\n")}\n`;
      const label = jobLabels[state.job.kind] || "Local operation";
      const completed = terminalStates.has(snap.status);
      const message = completed
        ? `${label}: ${snap.status}. ${snap.result?.detail || snap.result?.error || (successStates.has(snap.status) ? "Readiness and delivery evidence are shown separately below." : "Review the reported error and job details before retrying.")}`
        : `${label}: ${snap.status || "running"}${snap.progress?.detail ? ` - ${snap.progress.detail}` : ""}`;
      if ($("#job-status").textContent !== message) $("#job-status").textContent = message;
      $("#cancel-job").hidden = completed || !canCancel(state.job);
      if (completed) {
        const finished = state.job;
        const origin = state.jobOrigin;
        state.job = null;
        $("#cancel-job").hidden = true;
        renderControls();
        if (!successStates.has(snap.status)) showError(new Error(snap.result?.error || `${label} ${snap.status}. See job details; no automatic retry was attempted.`));
        await loadQueues();
        const target = snap.result?.queue;
        if (successStates.has(snap.status) && target && (!state.queue || state.queue === origin)) {
          await selectQueue(target, true);
          if (finished.kind === "refresh-delivery" && state.queue === target) showScreen("delivery");
        } else if (state.queue) {
          await refreshQueue();
          if (successStates.has(snap.status) && state.queue === origin) {
            if (finished.kind === "prepare" || finished.kind === "reschedule" || finished.kind === "stage-approval") showScreen("review");
            if (finished.kind === "approve" || finished.kind === "refresh-delivery") showScreen("delivery");
          }
        }
        break;
      }
      // One request completes before the delay and next request begin.
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  } catch (error) {
    state.pollFailed = !!state.job;
    $("#job-status").textContent = state.job
      ? `Progress connection failed: ${error.message}. Work may still be running.`
      : `The job ended, but the batch could not refresh: ${error.message}. Refresh before continuing.`;
    $("#retry-job").hidden = !state.job;
    showError(error);
  } finally { state.polling = false; renderControls(); }
}
async function cancelJob() {
  if (!state.job) throw new Error("No active local job to cancel.");
  if (!canCancel(state.job)) throw new Error("This operation cannot be cancelled safely. Wait for it to finish.");
  const result = await post(`/api/jobs/${encodeURIComponent(state.job.id)}/cancel`);
  tell(result.job?.status === "cancelled" ? "Local job cancelled. Refreshing its final state." : "Cancellation requested. Waiting for the owned process to stop.");
  if (!state.polling) void pollJob();
}
async function loadTrash() {
  const result = await api("/api/trash");
  $("#trash-list").innerHTML = (result.entries || []).map((entry) => {
    const id = typeof entry === "string" ? entry : entry.entry || entry.name || entry.id;
    return `<div class="notice"><p>${esc(typeof entry === "string" ? entry : entry.label || entry.queue || id)}</p><button type="button" data-restore="${esc(id)}">Restore removed item</button></div>`;
  }).join("") || '<p class="muted">No removed items available.</p>';
  $$("[data-restore]").forEach((button) => { button.disabled = isLocked(); });
}
async function restoreEntry(entry) {
  if (isLocked()) throw new Error("Wait for current work before restoring.");
  state.busy = true;
  renderControls();
  try {
    const result = await post("/api/restore", { entry });
    await loadTrash();
    await loadQueues();
    if (state.queue) await refreshQueue();
    tell(result.detail || "Restored the removed item. Review its media and schedule before approval.");
  } finally { state.busy = false; renderControls(); }
}
async function removeBatch() {
  if (!confirm(`Remove ${state.queue} from the local workspace? It moves to Recently removed. This does not unpublish or cancel any provider post.`)) return;
  if (isLocked()) throw new Error("Wait for current work before removing a batch.");
  const snapshot = batchSnapshot();
  state.busy = true;
  renderControls();
  try {
    await post(endpoint(snapshot.queue, "delete"), {}, snapshot.revision);
    state.queueRequest += 1;
    state.queueAbort?.abort();
    state.queue = null;
    state.data = null;
    remember(null);
    showScreen("batches");
    await loadQueues();
    tell("Batch moved to Recently removed. Provider posts were not changed.");
  } finally { state.busy = false; renderControls(); }
}
async function runAction(action, button) {
  if (["batches", "images", "review", "delivery"].includes(action)) return showScreen(action);
  if (action === "new") { $("#new-batch").open = true; $("#gen-lane").focus(); return; }
  if (action === "resume") return showScreen(preferredScreen(state.data));
  if (action === "prepare-screen") return showScreen("prepare");
  if (action === "copy") return copyGroup();
  if (action === "match") { $("#reconcile").scrollIntoView(); $("#staged-list select")?.focus(); return; }
  if (action === "reschedule") return openReschedule();
  if (action === "approve") return openApproval();
  if (action === "generate") return startJob("generate", { lane: $("#gen-lane").value, date: $("#gen-date").value });
  if (["prepare", "stage-approval", "refresh-delivery"].includes(action)) return startJob(action);
  if (action === "compare") return showComparison(button.dataset.image);
  if (action === "view-media") return showFullMedia(button.dataset.post, Number(button.dataset.media));
  if (action === "edit-post") return editPost(button.dataset.post);
  if (action === "edit-image") return editImage(button.dataset.image);
  if (action === "undo-image") {
    await mutate("undo-image", { image_id: button.dataset.image });
    $("#media-dialog").close();
    tell("Previous image version restored. Review readiness and prepare any affected previews.");
  }
  if (action === "delete-post") {
    if (!confirm("Remove this post locally? It can be restored from Recently removed. This does not unpublish it.")) return;
    const result = await mutate("delete-post", { post_id: button.dataset.post });
    tell(result.deleted?.summary_stale ? "Post removed. The batch summary also needs updating before approval." : "Post moved to Recently removed.");
  }
}
function guarded(handler) {
  return (event) => { Promise.resolve().then(() => handler(event)).catch(handleError); };
}
async function downloadBatch() {
  const snapshot = batchSnapshot();
  const blob = await api(endpoint(snapshot.queue, "download"), { responseType: "blob" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${snapshot.queue.replace(/\.json$/, "")}-ready.zip`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  tell("Final assets and captions downloaded. This does not approve or schedule posts.");
}
function wire() {
  $("#dismiss-error").addEventListener("click", () => { $("#error").hidden = true; });
  $("#refresh-queues").addEventListener("click", guarded(loadQueues));
  $("#refresh-batch").addEventListener("click", guarded(refreshQueue));
  $("#gen-lane").addEventListener("change", chooseLane);
  $("#gen-date").addEventListener("change", renderControls);
  $("#new-batch").addEventListener("toggle", renderControls);
  $("#prompt-group").addEventListener("change", () => { state.group = Number($("#prompt-group").value); renderGroup(); });
  $("#image-files").addEventListener("change", guarded(async (event) => {
    const files = Array.from(event.target.files);
    event.target.value = "";
    await uploadFiles(files);
  }));
  const dropzone = $("#dropzone");
  ["dragenter", "dragover"].forEach((type) => dropzone.addEventListener(type, (event) => { event.preventDefault(); dropzone.classList.add("over"); }));
  ["dragleave", "drop"].forEach((type) => dropzone.addEventListener(type, (event) => { event.preventDefault(); dropzone.classList.remove("over"); }));
  dropzone.addEventListener("drop", guarded((event) => uploadFiles(Array.from(event.dataTransfer.files))));
  $("#edit-form").addEventListener("submit", (event) => { event.preventDefault(); guarded(() => saveEdit(event.target))(event); });
  $("#reschedule-form").addEventListener("submit", (event) => {
    event.preventDefault();
    guarded(async () => {
      if (state.edit.queue !== state.queue || state.edit.revision !== state.data?.revision) throw new Error("Batch changed. Reopen the schedule editor after reviewing it.");
      await startJob("reschedule", { date: $("#reschedule-date").value });
      $("#reschedule-dialog").close();
    })(event);
  });
  $("#approve-confirm").addEventListener("change", renderControls);
  $("#approve-submit").addEventListener("click", guarded(approveDisplayed));
  $("#cancel-job").addEventListener("click", guarded(cancelJob));
  $("#retry-job").addEventListener("click", () => { void pollJob(); });
  $("#delete-batch").addEventListener("click", guarded(removeBatch));
  $("#download-batch").addEventListener("click", (event) => {
    event.preventDefault();
    guarded(downloadBatch)(event);
  });
  $("#refresh-trash").addEventListener("click", guarded(loadTrash));
  $("#trash-details").addEventListener("toggle", guarded(() => $("#trash-details").open ? loadTrash() : undefined));
  document.addEventListener("click", guarded(async (event) => {
    const button = event.target.closest("button");
    if (!button || button.disabled) return;
    if (button.hasAttribute("data-close-dialog")) { button.closest("dialog").close(); return; }
    if (button.dataset.queue) return selectQueue(button.dataset.queue);
    if (button.dataset.screen) return showScreen(button.dataset.screen);
    if (button.dataset.restore) return restoreEntry(button.dataset.restore);
    if (button.dataset.action) return runAction(button.dataset.action, button);
  }));
  document.addEventListener("submit", (event) => {
    if (!event.target.matches("[data-assign-file]")) return;
    event.preventDefault();
    guarded(async () => {
      await mutate("assign", { file: event.target.dataset.assignFile, image_id: new FormData(event.target).get("image_id") });
      tell("Image assigned. Inspect the match before preparing previews.");
    })(event);
  });
  document.addEventListener("change", guarded(async (event) => {
    if (!event.target.matches("[data-replace-image]")) return;
    const file = event.target.files[0];
    if (!file) return;
    event.target.value = "";
    const headers = fileHeaders(file);
    if (headers["Content-Type"] === "application/zip") throw new Error("Replacement takes one image, not a ZIP.");
    headers["X-Image-Id"] = event.target.dataset.replaceImage;
    await mutate("replace", file, headers);
    $("#media-dialog").close();
    tell("Replacement imported; the prior source is preserved. Prepare the changed previews before approval.");
  }));
  $$("dialog").forEach((dialog) => dialog.addEventListener("close", () => {
    dialog.querySelectorAll("video").forEach((video) => video.pause());
    if (state.dialogFocus?.isConnected) state.dialogFocus.focus();
    else $(`#heading-${state.screen}`).focus();
  }));
}
async function init() {
  wire();
  try {
    const session = await api("/api/session");
    if (!session.csrf || !Array.isArray(session.diagnostics)) throw new Error("The server does not provide the guided workspace session contract. Start the updated local server.");
    state.csrf = session.csrf;
    $("#diagnostics").innerHTML = session.diagnostics.map((item) => `<p><strong class="${item.ok ? "good" : "warning"}">${esc(item.name)}: ${item.ok ? "Available" : "Needs attention"}</strong><br>${esc(item.detail)}</p>`).join("");
    if (session.diagnostics.some((item) => !item.ok)) $("#diagnostics").closest("details").open = true;
    if (session.active_job) attachJob(session.active_job, null);
    await Promise.all([loadLanes(), loadQueues()]);
    state.initialized = true;
    const selected = recalledQueue();
    if (selected && state.queues.some((queue) => queue.name === selected)) await selectQueue(selected);
    else if (selected) {
      remember(null);
      tell("The previously selected batch is no longer in the local queue. Choose another batch.");
    }
    renderControls();
  } catch (error) {
    showError(error);
    $("#next-hint").textContent = "Could not connect to the workspace. Start the updated local server, then reload. No action has been sent.";
    $("#next-action").textContent = "Connection unavailable";
    $("#next-action").disabled = true;
  }
}
document.addEventListener("DOMContentLoaded", init);
