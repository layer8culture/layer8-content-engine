# Manual (no-API) image mode

Run the content engine with **no image API at all**. Instead of calling
Azure/OpenAI Images, the engine hands you a copy-paste prompt per image; you
generate them in ChatGPT or Copilot, drop the files into an inbox folder, and the
engine finishes them exactly like the API path would — crop to aspect, upscale to
a 2K master, composite the brand headline, and write the paths back into the queue.

Nothing else changes: the same queue schema, the same `assets/generated/` outputs,
the same publisher.

> **There is a web UI for this.** `python scripts/adhoc_server.py` runs the whole
> workflow below from a browser — including consuming the single `.zip` ChatGPT
> returns, instead of saving each file by hand, and preparing an approval PR. See
> [`adhoc-web-ui.md`](adhoc-web-ui.md). The commands below remain the reference path.

## What you need

- Python 3.12 (3.11 works) and `pip install pillow` — **no `openai` package, no keys**
- ffmpeg and ffprobe only if the batch contains reels or video carousel slides

## The commands

```bash
# 1. Write the prompt pack (no API calls, queue is left untouched)
python scripts/openai_gen.py queue/2026-08-18.json --manual

# 2. ... generate the images by hand, save them into assets/manual-inbox/

# 3. Prepare changed images and required videos together (strictly offline)
python scripts/prepare_media.py queue/2026-08-18.json

# 4. Optional: build the PR preview
python scripts/build_pr_preview.py queue/2026-08-18.json --repo <owner/repo> --sha <sha> --out pr-body.md

# 5. Prepare the approval PR; publishing still requires its approved merge
python scripts/ship_queue.py queue/2026-08-18.json --dry-run
python scripts/ship_queue.py queue/2026-08-18.json
```

## Step 1 — the prompt pack

`--manual` writes `queue/<name>.prompts.md` next to the queue (same convention as
`.summary.md`) containing:

- a checklist of every image: filename, post, format, aspect, canvas to request, headline
- one section per image with the **exact prompt to paste**, already composed per
  `brand/visual-style.md` by the generation step, plus the canvas size, a
  "render no text" rule, and which region to keep as clean negative space

One image is listed per post (`<post-id>.png`), or one per carousel slide
(`<post-id>-1.png`, `<post-id>-2.png`, …). Stories and reels default to 9:16.
Posts with `visual.source` of `library` or `reuse` are skipped — they don't need art.

Manual mode **does not modify the queue**. Nothing is marked `library` and no
`visual.file` is claimed until the image actually exists.

You can also force this mode without the flag by setting `IMAGE_BACKEND=manual`.
And if no credentials are configured at all, a normal run falls back to it
automatically instead of failing or degrading every post.

## Step 2 — generating the images

Paste a prompt block into ChatGPT / Copilot and ask for the image. Then:

- Save it as the **exact filename** the pack lists, into `assets/manual-inbox/`
  (e.g. `assets/manual-inbox/20260818-layer8culture-tiktok-hook-1.png`).
- `.png`, `.jpg`, `.jpeg` and `.webp` are all accepted; everything is normalized to PNG.
- The size doesn't have to be perfect — ingest center-crops to the post's aspect.
- **Never ask for text in the image.** Headlines and supporting lines are composited
  by the engine, which is why the prompts ask for clean negative space.

`assets/manual-inbox/` is gitignored, so raw drops never end up in a commit.

## Step 3 — ingest

`scripts/manual_media_ingest.py` applies the same finishing as the API path:

1. EXIF-transpose, convert to RGB
2. center-crop to the post's aspect ratio
3. resample so the long edge is the 2K master size (`OPENAI_IMAGE_LONG_EDGE`, default 2048)
4. composite brand typography — `brand_title_card` or `editorial_drop`, using the post's
   `visual.headline` / `subtext` / `accent` / `overlay_position`
5. write `assets/generated/<image-id>.png` and update `visual.file` / `visual.files`

Consumed drops move to `assets/manual-inbox/_ingested/`. Immutable originals also
live under `_ingested/_versions/<image-id>/<sha256>.<extension>`. Replacement never
destroys an earlier original; previous finished images, MP4s and covers are kept
under `.local/media/outputs/`. A settings change always reuses the original, not an
already-branded output. Re-generate an image by dropping a new file with the same
name and running Prepare previews again.

Useful flags:

| Flag | Effect |
| --- | --- |
| `--inbox <dir>` | read drops from somewhere else |
| `--out-dir <dir>` | write finished images somewhere else |
| `--keep` | leave consumed files in the inbox |
| `--dry-run` | report what would happen, write nothing |
| `--strict` | also exit non-zero while an expected original is missing |

Corrupt images, missing required fonts and failed typography always produce a
nonzero exit, even without `--strict`. Failed work preserves the input and any old
finished version; it does not advertise the old output as prepared. Dark sources
remain nonblocking warnings and are not automatically brightened.

The command is safe to run repeatedly — it reports what's still missing and only
wires up a carousel once **every** slide is present, so a half-finished batch can
never publish a short carousel. Ingest never replaces a reel's MP4 delivery path
with its still PNG. A changed still invalidates the reel and its reuse descendants
until successful video preparation.

## Incremental Prepare previews

`python scripts/prepare_media.py <queue>` finishes only stale or missing images,
then only required stale/missing reels, covers, video slides and reuse copies.
Image-only batches never need ffmpeg. An unchanged successful run preserves media
and queue modification times. Failed artifacts can be retried without redoing
successful independent work.

The local receipts in `.local/media/artifacts/` contain each artifact's original
or dependency content hashes, settings hash, renderer/font identity, finished
output hashes, status and warnings. An existing filename alone is not proof of
preparation. Pending source replacements, typography edits, changed renderers and
modified output bytes invalidate readiness. Local receipts do not contain keys
or other credentials and are not committed; CI separately validates the exact
committed media manifest.

The command exits nonzero on any failure. `--json <report-path>` additionally writes
`failed`, `warnings`, `prepared`, `unchanged`, `images_prepared` and
`videos_prepared`. Silent audio is a visible nonblocking warning, not a reason to
invent music. Missing required fonts, incomplete video/cover pairs and failed
renders block preparation.

For separate code and data checkouts, execute the script from the clean code
checkout and pass `--repo-root <data-root>`. Queues, originals, fonts, audio beds,
transcripts, finished media and local receipts resolve against that explicit data
root. Imports and renderer-code fingerprints continue to use the executing code,
not a possibly stale `scripts/` directory in the data checkout.

Application integrations use `prepare_media.prepare(queue_file: Path, repo_root:
Path) -> dict`, `preparation_status(posts, repo_root) -> {blockers, warnings}`, and
`invalidate(post_ids, repo_root) -> list[str]`. Invalidation marks recorded post
dependencies stale without deleting files. Import/replacement code can call
`manual_media.snapshot_file(source, versions_root) -> Path` before overwriting a
source; the helper creates the content-addressed immutable version described above.

## Reels without an API

Prepare previews always uses local motion/clip rendering, even if Azure video
credentials happen to be present. It never calls Sora or an image API.

The separate `scripts/reel_gen.py` command retains the CI backend selection:
it renders through Azure Sora-2 only
when `AZURE_OPENAI_ENDPOINT` / `AZURE_OPENAI_API_KEY` are set; otherwise it falls
back to the ffmpeg "motion" renderer, which animates the manually generated still
and burns in the overlay beats locally. Run it **after** ingest, since it needs
`assets/generated/<post-id>.png` to exist.

## Notes

- CI is unchanged: the nightly workflows still use the image API and the human
  approval gate still applies. This mode is for running the engine locally.
- Queue paths written by ingest use forward slashes, so a batch prepared on Windows
  publishes correctly from Linux CI.
- Everything here is stdlib + Pillow. `scripts/manual_media.py` owns the shared list
  of images a queue needs, so the prompt pack and the ingest step can't drift.
