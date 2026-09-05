# Guided local content workspace

The Content Engine guides a batch through **Batches -> Get images -> Prepare
previews -> Review & approve -> Delivery**. Manual ChatGPT image generation is
the normal path. There is no browser-driven ChatGPT automation, image API
requirement, frontend framework, or frontend build step.

**Approval always goes through a merged PR.** Preparing media, downloading a
bundle, staging a PR, and merging it are not proof that anything is scheduled.
Delivery shows the separate provider evidence.

## Open the app

From the repository in PowerShell:

```powershell
.\scripts\start-webapp.ps1
```

The launcher checks <http://127.0.0.1:8765/api/session> first. If the guided app
is already healthy it simply opens your browser. Otherwise it starts
`scripts\adhoc_server.py`, waits for a successful probe, then opens the app.
It never kills an arbitrary process to free a port. If another listener or an
older app occupies the port, use an explicitly different port:

```powershell
.\scripts\start-webapp.ps1 -Port 8766
```

Use `-NoBrowser` to leave the server running without opening a tab. Startup logs
are written under ignored `.local\`. To run the server directly instead:

```powershell
python .\scripts\adhoc_server.py
```

The Python server also accepts `--port`, `--no-browser`, and `--verbose`.
It binds only to loopback; do not expose it through a tunnel or public host.

### Run updated app code with an existing data checkout

To keep updated app code isolated while using another checkout's queue and
assets, launch from the updated code directory with an explicit data root:

```powershell
.\scripts\start-webapp.ps1 -DataRoot 'C:\projects\layer8-content-engine'
# Equivalent direct-server invocation:
python .\scripts\adhoc_server.py --data-root 'C:\projects\layer8-content-engine'
```

This selects the existing content; it does not copy or overwrite that checkout's
application code. Actions taken in the app still affect the selected data root,
so do not use a live checkout as a test fixture. Keep the isolated code directory
available while the app runs. Startup logs remain under the launcher's code
directory in `.local\`.

With `-DataRoot`, an already-running app must report a matching `data_root` in
its session response before the launcher will reuse it. A different or unknown
root is rejected rather than silently opening the wrong workspace; choose a new
port or restart the known server deliberately.

### Prerequisites and diagnostics

Use Python 3.11 or newer and the existing engine dependencies. Pillow handles
manual images; ffmpeg is needed for video batches. Copilot CLI must be installed
and signed in to create new batches. Git and authenticated GitHub CLI access are
needed to stage and merge approval PRs. Missing prerequisites are shown under
**Batches -> Environment diagnostics**. The launcher never installs dependencies
or fixes credentials automatically.

Existing batches can still be inspected when generation tools are unavailable.
A missing tool is not a reason to regenerate a batch or bypass approval.

## 1. Batches

Choose an existing batch to resume its next step. Your browser remembers the
selected batch. Reloading reconnects to the server's active job rather than
starting the operation again.

For a new batch, expand **Create a new batch**, choose a brand, confirm its date
in **America/New_York**, and select **Create batch**. The date remains explicit
through the run. The three brand lanes stay separate:

| Brand | Queue name |
| --- | --- |
| Layer8Culture | `YYYY-MM-DD.json` |
| Layer8Culture Radio | `lofi-YYYY-MM-DD.json` |
| The Real Estate Deal Lab | `deallab-YYYY-MM-DD.json` |

The lane's available generation settings and prompts determine what can run.
The app does not reactivate paused generation or overwrite existing batches.

Only one mutation/job runs at a time. You can inspect another batch while a job
runs, but uploads, edits, preparation and approval wait. If progress disconnects,
use **Reconnect to progress**; do not repeat the operation on the assumption
that it failed. A server restart may report interrupted work, which is not
successful preparation.

## 2. Get images

The app presents **groups of up to four outstanding images**. Each image shows
its intended headline, scene information, aspect, and source status.

1. Select **Copy this prompt group**, then **Open ChatGPT**.
2. Paste the prompt and generate the images yourself. If clipboard access is
   unavailable, expand the prompt text and copy it manually.
3. Return with a ZIP or multiple individual PNG, JPEG or WebP files. Use the
   normal, keyboard-accessible file picker or drop files into the upload area.
4. Check each automatic match. Assign any unmatched files using their expected
   headline, scene and aspect, rather than guessing from file order.

Groups advance from actual import state, not from a click on Copy. Partial
uploads preserve accepted files. Multiple uploads run sequentially against a
fresh revision after each file; a failure stops the remaining uploads instead
of silently retrying them. Unassigned images are scoped to the selected batch.
You do not need to rename a file to assign it.

**Source imported** is not **Preview prepared**. ZIP warnings, rejected files
and image warnings remain visible. The app does not brighten dark images or
invent missing audio.

## 3. Prepare previews

Inspect the matches and select **Prepare previews** once. The engine finishes
changed sources, applies the intended crop and brand typography, renders required
stale/missing videos, and refreshes dependent cross-posts. Unchanged outputs are
reused; image-only batches do not require a separate video action.

Select **Compare / replace** on any image to see its original source and final
output side by side. Each opens at full resolution in a separate tab. Upload an
individual replacement from that dialog, or use **Undo last replacement** when
a prior version exists. Replacement preserves the original history and
invalidates affected previews. Prepare and review the changed output again.

**Edit headline** changes headline/subtext without editing JSON. Warnings and
blockers are separate: missing or invalid final media blocks approval, while
nonblocking quality warnings still need your attention.

Job details and logs are collapsed by default. Cancellation applies only to
owned local work and does not undo PR merges or provider submissions.

## 4. Review & approve

Every carousel slide is shown **once, in order, without truncation**. Review
each slide at full size, watch videos, and read the complete caption, hashtags,
first comment and YouTube title where applicable. Times are displayed using the
named Eastern timezone, including daylight/standard time.

**Edit post** lets you change the text fields or an explicit schedule timestamp.
Individual timestamps require a UTC offset so an ambiguous daylight-saving time
is not guessed. For a fresh daily plan, select **Reschedule batch**, choose an
Eastern date, and review the resulting times. An expired schedule is a distinct
blocker: existing dates never silently roll forward.

Approval is deliberately two steps:

1. **Stage approval PR** creates or updates the batch's exact candidate. Review
   the displayed PR link, content revision and full head SHA. Staging does not
   merge or approve it.
2. **Approve displayed revision** opens a confirmation dialog. Check that you
   reviewed every slide, video, caption and schedule, then select **Approve and
   merge displayed PR**. Only that PR and head are eligible.

The server checks freshness, revision identity, required checks and repository
permissions again before merging. A changed head or edited batch invalidates
the prior review. No automatic approval, generic auto-merge, administrative
bypass, or direct-to-main publishing is offered. If a check or permission blocks
the merge, read the actual error and use the PR link; do not bypass the gate.

Once media is prepared, **Download final assets & captions** provides a ZIP for
offline inspection or handoff. Downloading never approves or schedules posts.

## 5. Delivery

Read PR/workflow links separately from per-post delivery receipts. These are
**observed receipt statuses**, not a live provider status check. Recorded provider
states may include queued/scheduled, published, private upload, inbox-only,
skipped, failed, or unknown. A private YouTube upload is not a public video; a
TikTok inbox upload is not a published post. Optional channels may be intentionally
skipped.

Opening the screen only reads cached local evidence. **Refresh delivery status**
explicitly starts a durable job that fetches the latest GitHub workflow and
receipt evidence; the job reconnects after a reload. Like other batch actions,
it requires the current session token and revision. Network access happens only
on this explicit refresh, not on page load.

The screen shows when evidence was observed and surfaces known workflow
failures even if no provider receipt was written. If no receipts exist, the app
says so; workflow success alone is not proof of publication. Refreshing never
submits posts, reruns publishing, or retries provider delivery. Unknown or
ambiguous attempts need reconciliation before recovery to avoid duplicate posts.

## Removal and recovery

**Batch details -> Remove batch** and individual **Post details -> Remove this
post** move local content into recoverable trash. **Batches -> Recently removed**
offers restoration when destination names do not collide. Shared-media ownership
is checked by the server; do not manually overwrite another batch's files.

Removing or restoring local content does not unpublish provider posts or cancel
their schedules. Recheck media and schedule readiness after restoration.
If a removed post leaves the narrative summary stale, the app reports it rather
than pretending that summary was rewritten.

## Local safety and troubleshooting

The app uses same-origin/Host checks, a local session token on every POST and a
current revision on batch writes. Tokens come from the session API and are not
stored in browser local storage. Queue JSON remains the content source of truth;
ignored `.local\` state records durable jobs and revision/approval references.

If another tab changes a batch, a stale action is rejected. The app refreshes
the current revision but never silently retries the write. Session expiry,
missing routes, failed operations and lost connections appear as explicit errors.
If you updated the code while an older server was running, restart that known
server or use a new port rather than working around missing routes.

The interface retains the existing electric blue/deep black brand tokens and
self-hosted Space Grotesk and Inter fonts from `assets\fonts\`, served by the
server's allowlisted `/fonts/` route. No font CDN is needed.

## Isolated browser coverage

The optional browser suite uses already-installed Playwright and Chromium.
It intercepts all application requests with in-memory fixtures and never opens
live user queues, generates content or mutates GitHub/provider accounts:

```powershell
python -m unittest discover -s tests -p test_webapp_browser.py
```

The suite skips if optional browser tooling is absent. Backend regression suites
remain independent; no runtime dependency is added for the frontend.
