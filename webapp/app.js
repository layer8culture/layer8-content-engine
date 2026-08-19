/* Layer8 ad-hoc run UI — talks to scripts/adhoc_server.py. No framework, no build. */
"use strict";

const state = {
  queue: null,      // selected queue filename
  queueInfo: null,  // that queue's row from /api/queues
  lanes: [],        // generation lanes
  lane: null,       // selected lane id
  images: [],       // ImageSpec payloads
  staged: [],       // files waiting to be assigned
  posts: [],        // post payloads for the review step
  polling: null,    // active job poll timer
};

/* ---------------- helpers ---------------- */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function esc(value) {
  return String(value === null || value === undefined ? "" : value)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

let toastTimer = null;
function toast(message, bad = false) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.toggle("bad", !!bad);
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), bad ? 6000 : 2800);
}

async function api(path, options) {
  const res = await fetch(path, options);
  const isJson = (res.headers.get("Content-Type") || "").includes("json");
  const body = isJson ? await res.json() : await res.text();
  if (!res.ok) {
    throw new Error((body && body.error) || String(body) || `HTTP ${res.status}`);
  }
  return body;
}

async function copyText(text, label) {
  try {
    await navigator.clipboard.writeText(text);
    toast(`${label} copied`);
    return;
  } catch (e) { /* fall through to the legacy path */ }
  const area = document.createElement("textarea");
  area.value = text;
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.appendChild(area);
  area.select();
  try {
    document.execCommand("copy");
    toast(`${label} copied`);
  } catch (e) {
    toast("Could not copy — select the text manually", true);
  } finally {
    document.body.removeChild(area);
  }
}

function setLocked(locked) {
  $$(".step").forEach((step) => {
    if (step.dataset.step === "0" || step.dataset.step === "1") return;
    step.classList.toggle("locked", locked);
  });
}

/* ---------------- step 0: generate ---------------- */
async function loadLanes() {
  const list = $("#lane-list");
  const warn = $("#copilot-warning");
  try {
    const data = await api("/api/lanes");
    state.lanes = data.lanes;
    warn.classList.toggle("hidden", data.copilot.ok);
    if (!data.copilot.ok) warn.textContent = data.copilot.error;
    list.innerHTML = "";
    data.lanes.forEach((lane) => list.appendChild(laneRow(lane)));
    if (!state.lane && data.lanes.length) selectLane(data.lanes[0].lane);
    else renderGenTarget();
    $("#run-generate").disabled = !data.copilot.ok;
  } catch (e) {
    list.innerHTML = `<p class="muted">Could not load lanes: ${esc(e.message)}</p>`;
  }
}

function laneRow(lane) {
  const row = document.createElement("button");
  row.type = "button";
  row.className = "qrow lane" + (lane.lane === state.lane ? " active" : "");
  row.innerHTML = `
    <span>
      <span class="name">${esc(lane.label)}</span>
      <div class="meta">${esc(lane.blurb)}</div>
    </span>
    <span class="pills">${
      lane.prompt_ok ? "" : '<span class="pill bad">prompt missing</span>'
    }</span>`;
  row.addEventListener("click", () => selectLane(lane.lane));
  return row;
}

function selectLane(id) {
  state.lane = id;
  const lane = state.lanes.find((l) => l.lane === id);
  $$(".qrow.lane").forEach((r, i) =>
    r.classList.toggle("active", state.lanes[i] && state.lanes[i].lane === id)
  );
  if (lane) $("#gen-date").value = lane.default_date;
  renderGenTarget();
}

/* The filename a run would write, mirrored from the server's naming rule so the
   target is visible before committing to a multi-minute run. */
function renderGenTarget() {
  const lane = state.lanes.find((l) => l.lane === state.lane);
  const date = $("#gen-date").value;
  if (!lane || !date) {
    $("#gen-target").textContent = "";
    return;
  }
  const prefix = { layer8culture: "", lofi: "lofi-", deallab: "deallab-" }[lane.lane] || "";
  $("#gen-target").textContent = `→ queue/${prefix}${date}.json`;
}

async function startGeneration() {
  const lane = state.lanes.find((l) => l.lane === state.lane);
  const date = $("#gen-date").value;
  if (!lane || !date) {
    toast("Pick a lane and a date first", true);
    return;
  }
  await startAndWatch(
    "generation",
    () =>
      api("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lane: lane.lane, date }),
      }),
    "#generate-status",
    "#generate-log",
    async (snap) => {
      await Promise.all([loadQueues(), loadLanes()]);
      if (snap.status === "done" && snap.result && snap.result.queue) {
        await selectQueue(snap.result.queue);
      }
    }
  );
}

/* ---------------- step 1: queues ---------------- */
async function loadQueues() {
  const list = $("#queue-list");
  list.innerHTML = '<p class="muted">Loading…</p>';
  try {
    const data = await api("/api/queues");
    if (!data.queues.length) {
      list.innerHTML = '<p class="muted">No queue files found in queue/.</p>';
      return;
    }
    list.innerHTML = "";
    data.queues.forEach((q) => list.appendChild(queueRow(q)));
    const current = data.queues.find((q) => q.name === state.queue);
    if (current) {
      state.queueInfo = current;
      renderPrCommands();
    }
  } catch (e) {
    list.innerHTML = `<p class="muted">Could not load queues: ${esc(e.message)}</p>`;
  }
}

function queueRow(q) {
  const row = document.createElement("div");
  row.className = "qrow" + (q.name === state.queue ? " active" : "");
  const pills = q.error
    ? '<span class="pill bad">unreadable</span>'
    : [
        q.done ? `<span class="pill done">${q.done} done</span>` : "",
        q.ready ? `<span class="pill ready">${q.ready} ready</span>` : "",
        q.pending ? `<span class="pill pending">${q.pending} to make</span>` : "",
        !q.images ? '<span class="pill">no images</span>' : "",
      ].join("");

  const pick = document.createElement("button");
  pick.type = "button";
  pick.className = "qpick";
  pick.innerHTML = `
    <span>
      <span class="name">${esc(q.name)}</span>
      <div class="meta">${esc(q.lane)} · ${q.posts} posts · ${q.images} images${
        q.error ? ` · ${esc(q.error)}` : ""
      }</div>
    </span>
    <span class="pills">${pills}</span>`;
  pick.addEventListener("click", () => selectQueue(q.name));

  const del = document.createElement("button");
  del.type = "button";
  del.className = "del";
  del.title = `Move ${q.name} and its media to .trash/`;
  del.textContent = "Delete";
  del.addEventListener("click", (e) => {
    e.stopPropagation();
    deleteQueue(q);
  });

  row.append(pick, del);
  return row;
}

async function deleteQueue(q) {
  const ok = confirm(
    `Delete ${q.name}?\n\nIt, its summary/prompt siblings and the media for its ` +
      `${q.posts} post(s) move to .trash/ — nothing is erased, and files another ` +
      `queue still uses are left alone.`
  );
  if (!ok) return;
  try {
    const data = (await api(`/api/queue/${encodeURIComponent(q.name)}/delete`, {
      method: "POST",
    })).deleted;
    if (state.queue === q.name) {
      state.queue = null;
      state.images = [];
      state.staged = [];
      state.posts = [];
      $("#queue-badge").classList.add("hidden");
      setLocked(true);
    }
    toast(`${q.name} → ${data.trash} (${data.media.length} file(s))`);
    await Promise.all([loadQueues(), loadLanes()]);
  } catch (e) {
    toast(e.message, true);
  }
}

async function selectQueue(name) {
  state.queue = name;
  $("#queue-badge").textContent = name;
  $("#queue-badge").classList.remove("hidden");
  $$(".qrow").forEach((r) => r.classList.remove("active"));
  setLocked(false);
  await Promise.all([refreshQueue(), loadBatchPrompt()]);
  await loadQueues();
  renderPrCommands();
  $("#step-prompts").scrollIntoView({ behavior: "smooth", block: "start" });
}

/* ---------------- step 2: prompts ---------------- */
async function loadBatchPrompt() {
  if (!state.queue) return;
  const pending = $("#pending-only").checked ? "1" : "0";
  try {
    const text = await api(
      `/api/queue/${encodeURIComponent(state.queue)}/batch-prompt?pending=${pending}`
    );
    $("#batch-prompt").textContent = text;
  } catch (e) {
    $("#batch-prompt").textContent = `Could not build the prompt: ${e.message}`;
  }
}

function renderImageList() {
  const box = $("#image-list");
  box.innerHTML = "";
  state.images.forEach((img) => {
    const row = document.createElement("div");
    row.className = "irow";
    row.innerHTML = `
      <span>
        <span class="fn">${esc(img.filename)}</span>
        <div class="meta muted">${esc(img.format)} · ${esc(img.aspect)} · ${esc(img.size)}</div>
      </span>
      <span class="pills"><span class="pill ${esc(img.status)}">${esc(img.status)}</span></span>`;
    const btn = document.createElement("button");
    btn.className = "small";
    btn.textContent = "Copy";
    btn.addEventListener("click", () => copyText(img.prompt, img.filename));
    row.appendChild(btn);
    box.appendChild(row);
  });
}

/* ---------------- step 3: upload + reconcile ---------------- */
async function uploadZip(file) {
  if (!state.queue) return;
  if (!/\.zip$/i.test(file.name)) {
    toast("That isn't a .zip file", true);
    return;
  }
  const report = $("#upload-report");
  report.classList.remove("hidden");
  report.innerHTML = `<p class="muted">Uploading ${esc(file.name)}…</p>`;
  try {
    const data = await api(`/api/queue/${encodeURIComponent(state.queue)}/upload`, {
      method: "POST",
      headers: { "Content-Type": "application/zip" },
      body: file,
    });
    state.images = data.images;
    state.staged = data.staged;
    renderUploadReport(data);
    renderImageList();
    renderStaged();
    toast(`${data.matched.length} image(s) matched automatically`);
  } catch (e) {
    report.innerHTML = `<p class="pill bad">Upload failed: ${esc(e.message)}</p>`;
    toast(e.message, true);
  }
}

function renderUploadReport(data) {
  const pending = state.images.filter((i) => i.status === "pending").length;
  const parts = [
    `<strong>${data.stored.length}</strong> image(s) read from the zip · ` +
      `<strong>${data.matched.length}</strong> matched a slot · ` +
      `<strong>${data.staged.length}</strong> need a decision · ` +
      `<strong>${pending}</strong> slot(s) still empty`,
  ];
  if (data.skipped.length) {
    parts.push(
      `<ul><li>skipped: ${data.skipped.map(esc).join("</li><li>skipped: ")}</li></ul>`
    );
  }
  $("#upload-report").innerHTML = parts.join("");
}

function renderStaged() {
  const wrap = $("#reconcile");
  const list = $("#staged-list");
  list.innerHTML = "";
  if (!state.staged.length) {
    wrap.classList.add("hidden");
    return;
  }
  wrap.classList.remove("hidden");

  const open = state.images.filter((i) => i.status === "pending");
  state.staged.forEach((item) => {
    const card = document.createElement("div");
    card.className = "staged";

    const why =
      item.reason === "suspect"
        ? `<span class="pill bad">name says ${esc(item.name_matches)}, shape disagrees</span>`
        : item.reason === "slot already filled"
        ? '<span class="pill">slot already filled</span>'
        : '<span class="pill pending">no matching name</span>';

    card.innerHTML = `
      <img src="${esc(item.url)}" alt="${esc(item.file)}" loading="lazy">
      <div>
        <div class="fn">${esc(item.file)}</div>
        <div class="meta muted">${esc(item.size)}${
          item.ratio ? ` · ratio ${item.ratio}` : ""
        }</div>
        <div class="pills" style="margin-top:8px">${why}</div>
      </div>`;

    const cell = document.createElement("div");
    cell.className = "assign-cell";
    const select = document.createElement("select");
    select.innerHTML =
      '<option value="">Assign to…</option>' +
      open
        .map(
          (i) =>
            `<option value="${esc(i.image_id)}">${esc(i.filename)} — ${esc(
              i.aspect
            )} ${esc(i.format)}</option>`
        )
        .join("");
    const btn = document.createElement("button");
    btn.className = "primary small";
    btn.textContent = "Assign";
    btn.addEventListener("click", () => {
      if (!select.value) {
        toast("Pick a slot first", true);
        return;
      }
      assign(item.file, select.value);
    });
    cell.appendChild(select);
    cell.appendChild(btn);
    card.appendChild(cell);
    list.appendChild(card);
  });
}

async function assign(file, imageId) {
  try {
    const data = await api(`/api/queue/${encodeURIComponent(state.queue)}/assign`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file, image_id: imageId }),
    });
    state.images = data.images;
    state.staged = data.staged;
    renderImageList();
    renderStaged();
    toast(`${file} → ${data.assigned.file}`);
  } catch (e) {
    toast(e.message, true);
  }
}

/* ---------------- steps 4/5: jobs ---------------- */
async function runJob(kind, statusEl, logEl, onDone) {
  if (!state.queue) return;
  await startAndWatch(
    kind,
    () => api(`/api/queue/${encodeURIComponent(state.queue)}/${kind}`, { method: "POST" }),
    statusEl,
    logEl,
    onDone
  );
}

/* Kicks off a job, then streams its log until it stops. `start` returns the
   POST response; everything after that is identical for every job kind. */
async function startAndWatch(kind, start, statusEl, logEl, onDone) {
  const status = $(statusEl);
  const log = $(logEl);
  log.textContent = "";
  log.classList.remove("hidden");
  status.className = "jobstatus running";
  status.textContent = "Starting…";
  $$("button.primary").forEach((b) => (b.disabled = true));

  let job;
  try {
    job = (await start()).job;
  } catch (e) {
    status.className = "jobstatus failed";
    status.textContent = e.message;
    $$("button.primary").forEach((b) => (b.disabled = false));
    toast(e.message, true);
    return;
  }

  status.textContent = `Running: ${job.command}`;
  let cursor = 0;
  clearInterval(state.polling);
  state.polling = setInterval(async () => {
    let snap;
    try {
      snap = await api(`/api/jobs/${job.id}?since=${cursor}`);
    } catch (e) {
      clearInterval(state.polling);
      status.className = "jobstatus failed";
      status.textContent = e.message;
      $$("button.primary").forEach((b) => (b.disabled = false));
      return;
    }
    if (snap.lines.length) {
      log.textContent += snap.lines.join("\n") + "\n";
      log.scrollTop = log.scrollHeight;
    }
    cursor = snap.next;
    if (snap.status !== "running") {
      clearInterval(state.polling);
      $$("button.primary").forEach((b) => (b.disabled = false));
      const ok = snap.status === "done";
      status.className = `jobstatus ${snap.status}`;
      status.textContent = ok
        ? "Finished."
        : `Failed (exit ${snap.returncode}). See the log above.`;
      toast(ok ? `${kind} finished` : `${kind} failed`, !ok);
      await refreshQueue();
      if (onDone) onDone(snap);
    }
  }, 900);
}

/* ---------------- rendering ---------------- */
async function refreshQueue() {
  if (!state.queue) return;
  try {
    const data = await api(`/api/queue/${encodeURIComponent(state.queue)}`);
    state.images = data.images;
    state.staged = data.staged;
    state.posts = data.posts;
    renderImageList();
    renderStaged();
    renderStills();
    renderReels();
    renderReview();
  } catch (e) {
    toast(e.message, true);
  }
}

function tile(url, caption, isVideo) {
  const media = isVideo
    ? `<video src="${esc(url)}" controls muted playsinline preload="metadata"></video>`
    : `<img src="${esc(url)}" alt="${esc(caption)}" loading="lazy">`;
  return `<div class="tile">${media}<div class="cap">${esc(caption)}</div></div>`;
}

function renderStills() {
  const done = state.images.filter((i) => i.status === "done" && i.preview);
  $("#still-grid").innerHTML = done
    .map((i) => tile(`${i.preview}?t=${Date.now()}`, i.filename, false))
    .join("");
}

function renderReels() {
  const seen = new Set();
  const html = [];
  state.posts.forEach((p) => {
    (p.previews || []).forEach((m) => {
      if (!m.is_video || seen.has(m.name)) return;
      seen.add(m.name);
      html.push(tile(`${m.url}?t=${Date.now()}`, m.name, true));
    });
  });
  $("#reel-grid").innerHTML = html.join("");
}

function renderReview() {
  const list = $("#review-list");
  list.innerHTML = "";
  state.posts.forEach((p) => {
    const card = document.createElement("div");
    card.className = "post";
    const media = (p.previews || [])
      .slice(0, 3)
      .map((m) =>
        m.is_video
          ? `<video src="${esc(m.url)}" controls muted playsinline preload="metadata"></video>`
          : `<img src="${esc(m.url)}" alt="${esc(m.name)}" loading="lazy">`
      )
      .join("");
    const when = p.schedule_time ? p.schedule_time.replace("T", " ") : "no time";
    card.innerHTML = `
      <div class="media">${media || '<div class="cap muted">no media yet</div>'}</div>
      <div>
        <div class="post-head">
          <h4>${esc(p.id)}</h4>
          <button type="button" class="del" data-id="${esc(p.id)}">Delete</button>
        </div>
        <div class="pills">
          <span class="pill">${esc(p.platform)}</span>
          <span class="pill">${esc(p.format)}</span>
          ${p.hook_score ? `<span class="pill ready">hook ${esc(p.hook_score)}</span>` : ""}
          <span class="pill">${esc(when)}</span>
        </div>
        ${p.youtube_title ? `<div class="meta muted" style="margin-top:8px">${esc(p.youtube_title)}</div>` : ""}
        <div class="caption" style="margin-top:10px">${esc(p.text)}</div>
        <div class="tags">${esc((p.hashtags || []).join(" "))}</div>
      </div>`;
    card.querySelector("button.del").addEventListener("click", () => deletePost(p));
    list.appendChild(card);
  });
}

async function deletePost(post) {
  const ok = confirm(
    `Delete post ${post.id}?\n\nIt is removed from ${state.queue} and its own media ` +
      `moves to .trash/. Media a remaining post still references — a reused reel ` +
      `master, for instance — stays put.`
  );
  if (!ok) return;
  try {
    const data = (await api(
      `/api/queue/${encodeURIComponent(state.queue)}/delete-post`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ post_id: post.id }),
      }
    )).deleted;
    toast(`${post.id} → ${data.trash} (${data.media.length} file(s))`);
    if (data.summary_stale) {
      toast(`Heads up: ${state.queue.replace(/\.json$/, ".summary.md")} still ` +
            "mentions it — edit that by hand.", true);
    }
    await Promise.all([refreshQueue(), loadQueues()]);
  } catch (e) {
    toast(e.message, true);
  }
}

/* The commands to open the approval PR. Emitted for the shell you're actually
   using: PowerShell chokes on `a && b` and on `cp src1 src2 dest/`, both of
   which fail quietly enough to push an empty branch. */
function prCommands(shell) {
  const stem = state.queue.replace(/\.json$/, "");
  const branch = `posts/${stem}`;
  const dir = `../pr-${stem}`;
  const prefix = assetPrefix();
  const glob = prefix ? `assets/generated/${prefix}-*` : "assets/generated/*";
  const sources = [`queue/${state.queue}`];
  if (state.queueInfo && state.queueInfo.summary) {
    sources.push(`queue/${stem}.summary.md`);
  }
  const preview =
    `python scripts/build_pr_preview.py queue/${state.queue}` +
    " --repo layer8culture/layer8-content-engine --sha $sha --out pr-body.md";
  const title = `Posts for ${stem}`;

  if (shell === "powershell") {
    return [
      "# built from origin/main in a worktree, because this clone is shallow",
      "git fetch origin",
      `git worktree add -b ${branch} ${dir} origin/main`,
      `Copy-Item ${sources.join(",")} -Destination ${dir}/queue/ -Force`,
      `Copy-Item ${glob} -Destination ${dir}/assets/generated/ -Force`,
      `Push-Location ${dir}`,
      "git add queue/",
      "git add -f assets/generated",
      `git commit -m "${title} - ready for review"`,
      `git push -u origin ${branch}`,
      "$sha = git rev-parse HEAD",
      preview,
      `gh pr create --base main --head ${branch} --title "${title}" --body-file pr-body.md`,
      "Pop-Location",
      `# once the PR is merged:  git worktree remove ${dir}`,
    ].join("\n");
  }
  return [
    "# built from origin/main in a worktree, because this clone is shallow",
    "git fetch origin",
    `git worktree add -b ${branch} ${dir} origin/main`,
    `cp ${sources.join(" ")} ${dir}/queue/`,
    `cp ${glob} ${dir}/assets/generated/`,
    `pushd ${dir}`,
    "git add queue/ && git add -f assets/generated",
    `git commit -m "${title} - ready for review"`,
    `git push -u origin ${branch}`,
    "sha=$(git rev-parse HEAD)",
    preview,
    `gh pr create --base main --head ${branch} --title "${title}" --body-file pr-body.md`,
    "popd",
    `# once the PR is merged:  git worktree remove ${dir}`,
  ].join("\n");
}

/* Media is named from the post ids, not the queue filename -- a lofi queue is
   `lofi-2026-08-18.json` but its assets are `20260818-lofi-...`. Read the real
   ids rather than mangling the filename. */
function assetPrefix() {
  const withId = state.posts.find((p) => /^\d{8}-/.test(String(p.id || "")));
  if (withId) return String(withId.id).slice(0, 8);
  const m = state.queue.match(/(\d{4})-(\d{2})-(\d{2})/);
  return m ? m[1] + m[2] + m[3] : "";
}

function renderPrCommands() {
  if (!state.queue) return;
  $("#pr-commands").textContent = prCommands($("#pr-shell").value);
}

/* ---------------- wiring ---------------- */
function init() {
  $("#refresh-queues").addEventListener("click", loadQueues);
  $("#refresh-lanes").addEventListener("click", loadLanes);
  $("#gen-date").addEventListener("change", renderGenTarget);
  $("#run-generate").addEventListener("click", startGeneration);
  $("#refresh-review").addEventListener("click", refreshQueue);
  $("#pending-only").addEventListener("change", loadBatchPrompt);
  $("#copy-all").addEventListener("click", () =>
    copyText($("#batch-prompt").textContent, "Prompt pack")
  );
  $("#copy-pr").addEventListener("click", () =>
    copyText($("#pr-commands").textContent, "Commands")
  );
  // Default to the shell you're most likely standing in.
  $("#pr-shell").value = /Win/i.test(navigator.platform || navigator.userAgent)
    ? "powershell"
    : "bash";
  $("#pr-shell").addEventListener("change", renderPrCommands);

  const dz = $("#dropzone");
  const input = $("#zip-input");
  input.addEventListener("change", () => {
    if (input.files[0]) uploadZip(input.files[0]);
    input.value = "";
  });
  ["dragenter", "dragover"].forEach((evt) =>
    dz.addEventListener(evt, (e) => {
      e.preventDefault();
      dz.classList.add("over");
    })
  );
  ["dragleave", "drop"].forEach((evt) =>
    dz.addEventListener(evt, (e) => {
      e.preventDefault();
      dz.classList.remove("over");
    })
  );
  dz.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file) uploadZip(file);
  });

  $("#run-ingest").addEventListener("click", () =>
    runJob("ingest", "#ingest-status", "#ingest-log")
  );
  $("#run-reels").addEventListener("click", () =>
    runJob("reels", "#reels-status", "#reels-log")
  );

  loadLanes();
  loadQueues();
}

document.addEventListener("DOMContentLoaded", init);
