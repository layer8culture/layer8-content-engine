# Layer8Culture Content Engine — web UI

A small **localhost** web app that drives a whole no-API image run from a browser:
generate a batch, pick a queue, copy one prompt block into ChatGPT, drop the returned
`.zip` back in, click through ingest and reel rendering, review every post, and publish.

It is a front-end for the existing scripts, not a replacement. `manual_media.plan_images`
stays the single source of truth for which images a queue needs, and ingest and reel
rendering run as the same subprocesses you would type by hand. Brand rules live in
`brand/` and are never duplicated here.

Prerequisite reading: [`manual-image-mode.md`](manual-image-mode.md) — this UI automates
exactly that workflow.

## Branding

The UI carries the layer8culture.io identity: the `LAYER8.CULTURE` lockup, electric
blue `#0047FF` on deep black, and the type pairing from
[`brand/brand-guidelines-v2.md`](../brand/brand-guidelines-v2.md) §14 — **Space Grotesk**
for display and labels, **Inter** for body copy.

Both typefaces are served from `assets/fonts/` by the server's `/fonts/` route, so the
UI is correct with no network and no CDN. That route uses an explicit allowlist
(`BRAND_FONTS` in `scripts/adhoc_server.py`) rather than a directory prefix, so it
cannot be used to read anything else in the repo.

## Start it

```bash
python scripts/adhoc_server.py
```

Opens <http://127.0.0.1:8765> automatically.

| Flag | Meaning |
| --- | --- |
| `--port N` | listen on a different port (default `8765`) |
| `--no-browser` | don't open a browser window |
| `--verbose` | log every HTTP request |

Requirements are the same as manual image mode: Python 3.11+, `pillow`, and ffmpeg
only if the batch contains reels. No API keys, no new dependencies — the server is
Python stdlib plus Pillow (used solely to read image dimensions).

## The steps

### 0. Generate a batch

Runs the **same Copilot CLI command the nightly workflow runs**, so a local batch and
a CI batch are produced identically. Pick a lane, confirm the date, press Generate;
the agent's log streams into the page and the finished queue file is selected for you.

| Lane | Prompt | Writes | Default date |
| --- | --- | --- | --- |
| Layer8Culture | `scripts/generation-prompt.md` | `queue/<date>.json` | tomorrow |
| Layer8Culture Radio | `scripts/generation-prompt-lofi.md` | `queue/lofi-<date>.json` | today |
| The Real Estate Deal Lab | `clients/therealestatedeallab/generation-prompt.md` | `queue/deallab-<date>.json` | today |

Defaults match each workflow's own choice, computed in `America/New_York`. Only the
Layer8Culture lane gets `web-fetch` and `--allow-all-urls`, mirroring
`generate-content.yml`; the other two are held to `read,write(queue/*)`.

Weekly Guide is deliberately absent — it is script-driven
(`research_weekly_guide.py` + `build_weekly_guide.py`) and needs the sibling site repo.

Choosing a date does **not** edit the prompt files. Those are shared with CI and must
not drift, so a date-override block is appended to the prompt text at run time.
Generation refuses to overwrite an existing queue file — delete it first.

It takes several minutes and holds the same one-job-at-a-time lock as ingest, which
is deliberate: generation must not race a run that is writing to the same queue.

> **The CLI must be installed and signed in.** If it isn't, step 0 says so instead of
> failing mid-run. On Windows the `copilot` first found on `PATH` is often a
> `.bat`/`.ps1` bootstrapper; those forward arguments through cmd.exe *and* PowerShell,
> which empties a multi-line prompt, so the server refuses them and looks for
> `copilot.exe` or the npm `npm-loader.js` (run via `node`) instead. Set
> `LAYER8_COPILOT_CLI` to point at a specific binary if yours lives somewhere unusual.

### 1. Pick a queue

Lists every `queue/*.json` with its lane (Layer8Culture / Layer8Culture Radio /
The Real Estate Deal Lab / Weekly Guide), post count, and how many images are
already done. Published batches live in `posted/` and are not listed.

**Delete** removes a whole batch: the `.json`, its `.summary.md` / `.prompts.md`
siblings, and the media belonging to its posts. See [Deleting](#deleting).

### 2. Copy the prompts

One **"Copy everything"** block containing a preamble that asks for a single zip with
exact filenames, then every image as `FILENAME:` plus the exact prompt and canvas —
the same text `--manual` writes to `queue/<name>.prompts.md`. Per-image copy buttons
are there as a fallback.

By default only outstanding images are included, so re-running a partly finished
batch doesn't regenerate work you already have.

### 3. Drop the zip

Drag the `.zip` ChatGPT returns onto the drop zone. The server extracts it and reports
what it stored, what it skipped, and what it could not place.

Only `.png`, `.jpg`, `.jpeg` and `.webp` are taken. Nested folders are flattened,
macOS `__MACOSX/` entries and dotfiles are dropped, and any entry whose path tries to
escape the staging directory is reduced to its bare filename.

#### Reconciling what didn't land

Two checks decide whether a file is accepted automatically, and **both** must pass:

1. its filename stem matches an outstanding image id (case-insensitively), and
2. its pixel shape agrees with that image's aspect.

Anything else is staged in `assets/manual-inbox/_unassigned/` and shown as a thumbnail
with a dropdown of the remaining slots:

| Reason | Meaning |
| --- | --- |
| `unmatched` | the name doesn't correspond to any image this queue needs |
| `suspect` | the name matches, but the shape belongs to a different aspect |
| `slot already filled` | that image already has a file |

`suspect` is the important one. Filenames alone have already proved untrustworthy
once — a batch whose files were assumed to be in prompt order would have mislabelled
8 of 19. A square dropped into a 9:16 slot is caught here rather than three steps later.

Assign the leftovers, then continue. Nothing is ingested until you do.

> The shape check accepts both the **generation canvas** the prompt asks for
> (`9:16` → 1024x1536) and the **final cropped** ratio, because a freshly generated
> portrait only becomes a true 9:16 after ingest crops it.

### 4. Finish the images (ingest)

Runs `scripts/manual_media_ingest.py` and streams its log. Each source is cropped to
its aspect, upscaled to a 2K master, given its composited brand headline, written to
`assets/generated/`, and wired back into the queue JSON. Sources are retired to
`assets/manual-inbox/_ingested/`.

### 5. Render the reels

Runs `scripts/reel_gen.py` and streams its log, then plays the results inline.

`reel_gen.py` always re-renders — there is no skip-if-exists — so this button is
idempotent but not free. On a lane whose audio bed is absent you will see a
"silent AAC track" note; that is expected, not an error.

### 6. Review and publish

Per-post preview: media, the exact caption, hashtags and schedule time. Each post also
has a **Delete** button, for dropping a single post from the batch.

Because you review the whole batch here, there is **no approval PR**. Once every image
is done, **Publish to main** commits the queue, its `.summary.md` and every asset
belonging to those post ids, pushes to `main`, and `publish.yml` schedules the posts in
Postiz. **Check without publishing** runs exactly the same validation and stops short of
the push.

The button stays disabled while any image is still pending, because a queue with a
missing file reaches Postiz and fails there with `missing_media`, leaving the day half
posted. `ship_queue.py` re-checks the same thing server-side, and also rejects a
carousel with a gap or a TikTok/YouTube post holding a still.

**How the push is made safe.** This clone is routinely shallow and behind
`origin/main`, so committing from it would revert whatever landed since. Instead the
commit is built in a throwaway worktree created from a freshly fetched `origin/main`,
pushed, and the worktree deleted — nothing in your working tree is ever pushed. If
someone else lands a commit first, the push rebases and retries. Publishing the same
batch twice is a no-op rather than an empty commit.

The assets are gathered from the post ids, not the queue filename — a lofi queue is
`lofi-2026-08-18.json` but its media is `20260818-lofi-…`. That also picks up files the
queue never names, such as a reel's `-cover.png`.

It can also be run without the UI:

```bash
python scripts/ship_queue.py queue/2026-08-20.json --dry-run   # validate only
python scripts/ship_queue.py queue/2026-08-20.json             # publish
```

## Deleting

Nothing is ever erased. Both delete buttons move what they remove into a timestamped
folder under `.trash/` with a `manifest.json` describing how to put it back:

| | Moves |
| --- | --- |
| Delete a post | its own media, plus `post.json` holding the removed entry; the queue JSON is rewritten without it |
| Delete a queue | the `.json`, its `.summary.md` / `.prompts.md` siblings, and the media for every post in it |

A post's media is resolved **exactly** — from the image plan and the paths the queue
actually records — never by globbing the post id, which would match `x-10` while
deleting `x-1`. Files a *remaining* post still references are left alone, so removing
a cross-posted Reel cannot take its TikTok master's video down with it.

`.trash/` is gitignored, so a deletion is never published. Prune it by hand.

> Deleting a post leaves `queue/<name>.summary.md` stale. That file is hand-written
> narrative, not derived, so it is flagged rather than silently rewritten — edit it
> before you publish.

Deleting is not unpublishing: anything already archived in `posted/` is untouched.

## Safety model

Single user, localhost, no authentication — stated plainly because it matters:

- binds `127.0.0.1` only, never `0.0.0.0`
- refuses to start unless it can see the repo layout it expects
- queue names must match `^[A-Za-z0-9][A-Za-z0-9._-]*\.json$` and are re-checked to
  resolve inside `queue/` after normalisation
- media paths are restricted to `assets/generated/` and the staging directory, and are
  likewise re-checked after resolution
- zip entries never keep their archive path; names are re-derived from the basename,
  so a crafted entry cannot escape the staging directory
- uploads are capped (512 MB total, 64 MB and 500 entries per archive)
- one job runs at a time; a second request gets `409`, and deleting is refused while
  a job is running
- generation and deletion are scoped to `queue/` and `assets/generated/`; deletions
  move files to `.trash/` rather than unlinking them
- publishing is the one action that leaves this machine. It pushes only from a
  worktree built off a freshly fetched `origin/main`, only the queue file and the
  assets belonging to its post ids, and only after every declared file is confirmed
  present — so an incomplete batch fails locally rather than in Postiz

Publishing from the UI replaces the approval PR **for ad-hoc batches you reviewed in
step 6**. The nightly workflows are unchanged: they still open a PR, because nobody
has looked at that content yet.

Do not expose the port. It runs your scripts by design.

## Tests

```bash
python -m unittest tests.test_adhoc_server tests.test_ship_queue
```

Covers entry sanitising, safe extraction (including zip-slip), the shape check, the
path guards, the batch prompt, the staging/assignment round trip, lane resolution and
generation argv, both deletions — including the shared-media case that must not
be collateral — and the publish route.

`tests.test_ship_queue` builds a real repo against a bare remote and pushes into it, so
the publish path is exercised end to end: it asserts an incomplete batch is refused, a
finished one lands with its summary and assets, another day's assets are left behind,
a second publish is a no-op, a push races and rebases, and — the one that matters most —
that publishing from a checkout which is behind the remote does not revert it.
