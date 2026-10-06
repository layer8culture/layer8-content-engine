# Layer8 Studio

Lean, laptop-run content engine for **Layer8Culture** and **Layer8Culture Radio**.
It replaces the GitHub Actions engine (still in the repo root as legacy).

*Technology has seven layers. We're the eighth.*

```
19:00 daily  run-daily  → news + transcript + history → Claude Opus 5.5 (Copilot CLI) → plan JSON
                          → render (Playwright stills, Remotion reels) → ChatGPT prompt pack
                          → quality gates → preview.html
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

The tasks use *StartWhenAvailable*, so a run missed while the laptop was off fires when it wakes.

## Commands

| Command | What it does |
|---|---|
| `python studio.py plan [--date D] [--force]` | Ask Opus for the day's plan → `data/plans/D.json` |
| `python studio.py render [--date D] [--only ID..]` | Render stills and reels → `data/media/D/` |
| `python studio.py ingest [--date D]` | Pick up ChatGPT heroes from `inbox/`, crop, and re-render |
| `python studio.py publish [--dry] [--now ISO]` | Push due posts that passed the gates to Postiz |
| `python studio.py run-daily [--date D]` | plan → render → prompt pack → gates → preview |
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

A failing post is blocked and logged; the others still go out. Every attempt is appended to `data/posted.jsonl`.

## ChatGPT hero images (manual, ~5 min/day)

1. After the 19:00 run, open `inbox\PROMPTS-<date>.md`.
2. Paste **Step 1** (the locked style preamble) into ChatGPT, then paste **Step 2** (the numbered shot list).
3. Save each image into `studio\inbox\` with the **exact filename** shown, e.g. `20261007-l8-01-hero.png`.
4. The next `publish` run, or `python studio.py ingest`, does the rest. It crops to 4:5 or 9:16, composites the brand type into the dark lower 45%, and swaps the image into the post.

If a hero image has not arrived by post time, the template version is posted instead. Studio never drives chatgpt.com.

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
