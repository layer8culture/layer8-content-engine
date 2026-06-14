# Layer8Culture Content Engine

An automated social media pipeline: **Copilot CLI** (brain) → **OpenAI** (visuals) → **PR approval** (you) → **Postiz** (posting) → **daily report** (back to you).

## How it works

```
transcripts/ + calendar/topics.md + brand/ + live AI-news research
        │
        ▼  (nightly GitHub Action)
  Copilot CLI researches the day's AI news + generates posts + visual prompts
        │
        ▼
  OpenAI API generates a fresh topical image per post
        │
        ▼
  Pull Request opened  ──►  YOU review/edit/merge (GitHub mobile)
        │
        ▼  (merge to main)
  Posts + media pushed to Postiz → scheduled
        │
        ▼  (every morning)
  Daily report: what posted, what's queued, what needs you
```

> **Current focus:** the engine generates 3-4 **layer8culture Instagram** posts
> per day (X, TikTok, and lofi are paused). Each post gets a fresh OpenAI image
> tied to that day's AI news — one high-quality hero per day, the rest medium.
> Flip the focus back on by editing `scripts/generation-prompt.md`.

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
   | `COPILOT_CLI_TOKEN` | A token the Copilot CLI accepts for authentication. A **fine-grained** PAT with the **Copilot Requests** permission works only if your account/enterprise policy allows PAT-based Copilot access. If that's blocked, use the OAuth token the CLI stores after you run `copilot` → `/login` with your Copilot-seat account (a `gho_` token; classic `ghp_` PATs are NOT supported). |
   | `ENGINE_PR_TOKEN` | A user PAT with `repo` scope, used **only** to open the nightly approval PR. Required because the org disables "Allow GitHub Actions to create pull requests", so the built-in `GITHUB_TOKEN` can't create it — a PR authored by a real user can. |
   | `OPENAI_API_KEY` | From platform.openai.com (image generation) |
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
- `assets/generated/` — OpenAI image outputs, named by post ID.
- `scripts/` — the machinery. `.github/workflows/` — the schedule.
