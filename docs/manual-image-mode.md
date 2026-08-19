# Manual (no-API) image mode

Run the content engine with **no image API at all**. Instead of calling
Azure/OpenAI Images, the engine hands you a copy-paste prompt per image; you
generate them in ChatGPT or Copilot, drop the files into an inbox folder, and the
engine finishes them exactly like the API path would — crop to aspect, upscale to
a 2K master, composite the brand headline, and write the paths back into the queue.

Nothing else changes: the same queue schema, the same `assets/generated/` outputs,
the same approval PR, the same publisher.

> **There is a web UI for this.** `python scripts/adhoc_server.py` runs the whole
> workflow below from a browser — including consuming the single `.zip` ChatGPT
> returns, instead of saving each file by hand. See
> [`adhoc-web-ui.md`](adhoc-web-ui.md). The commands below remain the reference path.

## What you need

- Python 3.12 (3.11 works) and `pip install pillow` — **no `openai` package, no keys**
- ffmpeg only if the batch contains reels

## The three commands

```bash
# 1. Write the prompt pack (no API calls, queue is left untouched)
python scripts/openai_gen.py queue/2026-08-18.json --manual

# 2. ... generate the images by hand, save them into assets/manual-inbox/

# 3. Finish them and wire them into the queue
python scripts/manual_media_ingest.py queue/2026-08-18.json

# 4. Optional: reels (ffmpeg only, no Sora needed) and the PR preview
python scripts/reel_gen.py queue/2026-08-18.json
python scripts/build_pr_preview.py queue/2026-08-18.json --repo <owner/repo> --sha <sha> --out pr-body.md
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

Consumed drops move to `assets/manual-inbox/_ingested/` so a re-run can never
double-composite typography onto an already-branded image. Re-generate an image by
dropping a new file with the same name and running ingest again.

Useful flags:

| Flag | Effect |
| --- | --- |
| `--inbox <dir>` | read drops from somewhere else |
| `--out-dir <dir>` | write finished images somewhere else |
| `--keep` | leave consumed files in the inbox |
| `--dry-run` | report what would happen, write nothing |
| `--strict` | exit non-zero while any expected image is still missing |

The command is safe to run repeatedly — it reports what's still missing and only
wires up a carousel once **every** slide is present, so a half-finished batch can
never publish a short carousel.

## Reels without an API

Reels still work offline. `scripts/reel_gen.py` renders through Azure Sora-2 only
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
