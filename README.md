# Layer8Culture Content Engine

An automated social media pipeline: **Copilot CLI** (brain) → **Higgsfield** (visuals) → **PR approval** (you) → **Postiz** (posting) → **daily report** (back to you).

## How it works

```
transcripts/ + calendar/topics.md + brand/
        │
        ▼  (nightly GitHub Action)
  Copilot CLI generates posts + visual prompts
        │
        ▼
  Higgsfield API generates images/animations
        │
        ▼
  Pull Request opened  ──►  YOU review/edit/merge (GitHub mobile)
        │
        ▼  (merge to main)
  Posts + media pushed to Postiz → scheduled to all platforms
        │
        ▼  (every morning)
  Daily report: what posted, what's queued, what needs you
```

## Setup (one-time)

1. **Create a private GitHub repo** and push this folder to it.
2. **Deploy Postiz** on your Azure VM:
   ```bash
   git clone https://github.com/gitroomhq/postiz-app
   # use their docker-compose; set up HTTPS with Caddy
   # connect your social accounts in the Postiz UI
   # create an API key in Postiz settings
   ```
3. **Add GitHub repo secrets** (Settings → Secrets → Actions):
   | Secret | What it is |
   |---|---|
   | `COPILOT_CLI_TOKEN` | GitHub PAT with Copilot access (your FTE account) |
   | `HF_API_KEY` / `HF_API_SECRET` | From cloud.higgsfield.ai |
   | `POSTIZ_URL` | e.g. `https://post.layer8culture.io` |
   | `POSTIZ_API_KEY` | From your Postiz instance |
   | `REPORT_WEBHOOK` | Discord/Slack webhook for the daily report (optional) |
4. **Customize the brand files** in `brand/` — this is what makes output sound like you.
5. **Enable the workflows** in the Actions tab.

## Weekly rhythm

| When | What you do | Time |
|---|---|---|
| After Tech Thursday | Drop transcript in `transcripts/`, update `calendar/topics.md` | 5 min |
| Nightly (automatic) | Engine opens a PR with tomorrow's posts + visuals | 0 min |
| Each morning | Review PR on GitHub mobile, edit, merge | 2-5 min |
| Each morning | Read the daily report | 1 min |

## Folder map

- `brand/` — canonical guidelines (brand-guidelines-v2.md) + operational digests (voice, visual style, hashtags). Read by every generation run.
- `calendar/topics.md` — your weekly steering input ("lean into X this week").
- `transcripts/` — Tech Thursday transcripts (pillar content).
- `queue/` — generated posts awaiting approval (the PR contents).
- `posted/` — archive of published posts (feeds the report + dedupe).
- `assets/library/` — your existing branded graphics (fallback pool).
- `assets/generated/` — Higgsfield outputs, named by post ID.
- `scripts/` — the machinery. `.github/workflows/` — the schedule.
