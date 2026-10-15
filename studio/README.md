# Layer8 Studio

Lean, laptop-run content engine for **Layer8Culture** and **Layer8Culture Radio**.
It replaces the GitHub Actions engine (still in the repo root as legacy).

*Technology has seven layers. We're the eighth.*

```
17:00 daily  run-daily  → deterministic bounded context → one tool-free Copilot JSON response
                          → render (Playwright stills, Remotion reels) → ChatGPT prompt pack
                          → quality gates → preview.html → queue tomorrow in Postiz → email
every 30 min publish    → ingest heroes from inbox/ → re-render → gates → Postiz (due posts only)
```

There is **no human approval step**. Safety comes from the gates and the kill switch.

## Setup (once)

1. `cd studio`
2. `pip install -r requirements.txt` and then `python -m playwright install chromium`
3. `cd templates\remotion; npm install; cd ..\..`
4. `copy .env.example .env` and fill in `POSTIZ_URL` (https://postiz.layer8culture.io) and `POSTIZ_API_KEY` (Postiz → Settings → Public API); channel IDs default from `config.yaml` (optional: webhook, `HIGGSFIELD_API_KEY`).
5. `copilot` → log in once if needed. The model is set by `model:` in `config.yaml`.
6. Fill in `facts:` in `config.yaml` (Tech Thursday time/URL, radio stream URL). The planner never states a fact that is blank there.
7. Test run: `python studio.py dry-run`. This plans and renders tomorrow without publishing. Open `data\media\<date>\preview.html` to review it.
8. Schedule it: `powershell -ExecutionPolicy Bypass -File install-schedule.ps1` (remove with `-Uninstall`).

The tasks use *StartWhenAvailable*, so a run missed while the laptop was off fires when it wakes. If the 17:00 plan is still missing after 18:00 for any reason, the next publish run makes it (`publish.catch_up_daily_after_hour`).

Tasks launch `task.ps1` through `conhost.exe --headless`, so Windows never hands them to Windows Terminal. On 10/7 a hung Terminal blocked every task launch. Each run writes `=== start` and `=== end … exit N` lines to `data\logs\task-<job>.log`. A run with a start line and no end line means it hung or was killed.

Planning is deliberately not an autonomous coding-agent loop. Python gathers and bounds the approved sources, assigns the required slots, sends one self-contained prompt to Copilot with file/shell/web tools disabled, captures the JSON response, writes it, and validates it. One malformed response gets one bounded repair call. The model never owns persistence, retries, scheduling, or success reporting. Per-stage state is written atomically to `data\runs\<date>.json`; a date-specific planning lock prevents duplicate writers without blocking the every-30-minute publisher.

For one daily email after next-day content is actually prepared, set `NOTIFY_EMAIL_TO` plus the SMTP fields in `.env`. A successful daily run now queues every validated route in Postiz before sending the completion email; the message identifies the Postiz queue state, failed gates, and missing heroes and attaches the one-paste prompt pack. A per-date marker prevents duplicate success emails when the 18:00 catch-up runs. If queueing or email fails, the run exits nonzero and the recovery run safely resumes because plans, posts, and emails are idempotent. Gmail users need an app password; Studio never uses or stores a normal Google password.

For Gmail, enable 2-Step Verification and create an app password yourself at <https://myaccount.google.com/apppasswords>. Then double-click `setup-email.cmd`, or run `powershell -ExecutionPolicy Bypass -File setup-email.ps1 -Test`. Password input is masked and never appears in the command line or shell history; the script preserves existing `.env` values, stores the app password only in gitignored `.env`, and sends one test email only to `NOTIFY_EMAIL_TO`. You can repeat just the test later with `python studio.py email-test`.

## Commands

| Command | What it does |
|---|---|
| `python studio.py plan [--date D] [--force]` | Ask the configured model for the day's plan → `data/plans/D.json` |
| `python studio.py render [--date D] [--only ID..]` | Render stills and reels → `data/media/D/` |
| `python studio.py heroes [--date D] [--downloads DIR]` | Manual ChatGPT hero desk: copies the prompts, watches Downloads, files each image, then ingests |
| `python studio.py ingest [--date D]` | Pick up ChatGPT heroes from `inbox/`, crop, and re-render |
| `python studio.py publish [--dry] [--date D] [--only ID..] [--ahead]` | Push due posts that passed the gates to Postiz (`--ahead`: schedule the whole day now at its planned times) |
| `python studio.py run-daily [--date D]` | plan → render → prompt pack → gates → preview → queue all routes → completion email |
| `python studio.py dry-run [--date D] [--reuse-plan]` | Same as run-daily, then a publish simulation (no network) |
| `python studio.py status [--date D]` | Plan, gates, heroes, and what has been posted |

Dates default to tomorrow for plan/run-daily/dry-run and to today otherwise.

## Pause / kill switch

- Create an empty file `studio\PAUSE` (`New-Item studio\PAUSE`) to stop all publishing immediately. Delete it to resume.
- Or set `paused: true` in `config.yaml`.
- Planning and rendering keep running while paused, so you can review previews.

## Quality gates (`studio_lib/gates.py`)

A post is published only if **all** of these pass:

- Schema is valid.
- No banned hype phrases, emoji, or excess exclamation marks.
- Hook and caption lengths are within limits; hashtag rules are met.
- No placeholders and no links outside the allowed domains.
- No unstated facts (show times, a 24/7 live claim).
- Radio posts stay off AI/tech topics.
- Reel beats stay short.
- No near-duplicate of the last 21 days (configurable) of `data/posted.jsonl` or of another post in the plan.
- Rendered media has the right size and duration.
- Text fits with no overflow and no headline/body overlap.

A daily preparation queues nothing unless every planned post passes. Every publish attempt is appended to `data/posted.jsonl`.

## ChatGPT hero images (manual, ~5 min/day)

After the 17:00 run you'll get a "Hero prompts ready" toast. Double-click **Layer8 Heroes** on your desktop (or run `studio\heroes.cmd`, or `python studio.py heroes [--date D]`):

1. One combined prompt (locked style + every missing shot) is already on your clipboard, and chatgpt.com opens in your browser. Start a new chat, paste once, and send. The prompt asks for every shot as a **separate downloadable image**, never a collage/contact sheet.
2. ChatGPT's current image interface may still return only one image per response; **ChatGPT Pro does not guarantee multi-image batch output**. If that happens, tell it `continue` for the remaining shots. Press **Enter** in the Heroes window anytime to copy a fresh combined prompt containing only shots still missing.
3. Click **download** on each image. Exact requested filenames are mapped automatically even if images finish out of order. For generic names such as `ChatGPT Image.png`, Studio uses the configured local Copilot model's vision support to compare visible scene content with the current prompt pack. A match is accepted only when it clears the configured confidence and runner-up margin and does not collide with another image. Otherwise Studio waits for you to press the displayed shot number; it never guesses from arrival order, timestamps, or browser `(1)` suffixes. It prints `✓ 2/4 saved as …` and warns if the aspect looks wrong.
   - Keys: `1`–`9` = map the pending generic download to that shot, `n` = explicitly map it to the next missing shot (sequential fallback), `r` = redo the last shot, `s` = skip the next missing shot (template fallback), `q` = quit, Enter = copy a remaining-shots prompt.
4. When every shot is filled it runs `ingest`, which crops to 4:5 or 9:16, composites the brand type into the dark lower 45%, and swaps the hero into the post.

Only images downloaded after you start count; partial downloads (`.crdownload`, `.tmp`), duplicate image bytes, and assets already filed in `inbox\_used` are ignored. The watcher runs only while the **Layer8 Heroes** window is open; scheduled daily/publish tasks do not continuously scan Downloads. To watch a different folder, set `heroes.downloads_dir` in `config.yaml`, set `STUDIO_DOWNLOADS_DIR`, or pass `--downloads`. You can still save images into `studio\inbox\` by hand with the exact filenames from `inbox\PROMPTS-<date>.md`.

Before the Heroes window closes, it prints a dated **HEROES / POSTIZ STATUS** summary. This reports how many images were matched, ingested, missing, or skipped; render and gate results; every route's planned ET time; and one of these truthful states: `LOCAL MEDIA READY`, `WAITING FOR PUBLISH`, `POSTIZ VERIFIED UPDATED`, `PAUSED`, `BLOCKED BY GATES`, `TOO CLOSE TO REPLACE`, or an explicit verification/error state. A local hero render alone is never described as uploaded. For tomorrow's images downloaded tonight, the normal first eligible publisher check is **06:00 ET tomorrow**, then every 30 minutes through 23:00; today's images use the next half-hour check. The displayed handoff is conditional on the laptop being on, the scheduled task succeeding, and the route remaining outside the 10-minute replacement cutoff. Network upload completion cannot be promised to the second. Rerunning Heroes after every image is already present performs no image work and refreshes this status.

If a hero image has not arrived by post time, the template version is posted instead. Studio never drives chatgpt.com: it only opens the URL, uses the clipboard, and watches your Downloads folder.

If the template version was already sent to Postiz (for example with `publish --ahead`), the publish run on the post's day swaps it: it deletes the queued Postiz post and re-schedules it at the same time with the hero version. Posts within 10 minutes of going out are left alone. To swap tomorrow's posts tonight, run `python studio.py ingest --date D`, then `python studio.py publish --date D --ahead`. To force a re-send, add `--replace --only <ids>`.

## Video extras

- **Radio loop clips:** drop `inbox\video\radio-<anything>.mp4`. The next plan adds a loop-reel (IG Reel + YouTube Short) built on it.
- **Premium clip for a specific post:** drop `inbox\video\<post-id>.mp4`. It becomes the background of that reel.
- **Higgsfield** (Tech Thursday/weekly hero video): set `HIGGSFIELD_API_KEY` and `higgsfield.model_path` and keep `monthly_credit_cap` > 0. Spend is tracked in `data/higgsfield-ledger.jsonl`. This feature is off by default. The API body shape is still a TODO in `studio_lib/higgsfield.py`.

## Layout

```
studio.py            CLI
config.yaml          all tunables (cadence, facts, gates, channels, model)
brand/               brand docs, voice digests, topics-*.md (edit these to steer)
transcripts/         Tech Thursday .vtt (drop new ones here; newest is used for recaps)
assets/fonts/ config/schedule-windows.json
templates/           layer8.css, radio.css, plan-prompt.md, remotion/
studio_lib/          planner, render, heroes, gates, publish, schedule, …
inbox/               ChatGPT heroes + video drops (gitignored)
data/                plans, media, logs, posted.jsonl (gitignored)
tests/               pytest — schema, gates, schedule
```

Run the tests with `python -m pytest -q`.
