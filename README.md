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
  OpenAI generates fresh visuals per post (image / carousel slides),
  ffmpeg renders Reels (video) from the stills
        │
        ▼
  Pull Request opened (with a visual preview)  ──►  YOU review/edit/merge (GitHub mobile)
        │
        ▼  (merge to main)
  Posts + media pushed to Postiz → scheduled (feed / carousel / Reel / story)
        │
        ▼  (every morning)
  Daily report + Instagram Insights feedback loop steers the next day
```

> **Two channels.** The engine runs **two separate pipelines**:
> - **layer8culture Instagram** (nightly) — news-driven posts across a deliberate
>   format mix: **Reels** (reach), **carousels** (saves), **Stories** (daily
>   engagement), and the occasional single static post. Each post gets fresh OpenAI
>   visuals tied to that day's AI news, one high-quality hero per day. A nightly
>   **analytics** job pulls Instagram Insights into `analytics/insights-digest.md`,
>   which feeds back into generation to grow toward 4k+ followers. Steer it via
>   `scripts/generation-prompt.md` and `calendar/topics.md`.
> - **Layer8Culture Radio (lofi) Instagram** (Mon/Wed/Fri) — calm, evergreen
>   focus-music brand content (quote cards, loop-preview Reels, playlist/community
>   posts), plus a "Now Live on YouTube" promo when a video link is supplied. Its own
>   prompt (`scripts/generation-prompt-lofi.md`), queue files (`queue/lofi-*.json`),
>   workflow (`generate-lofi.yml`), and approval PR. Steer it via
>   `calendar/topics-lofi.md`. (X and TikTok remain paused for both brands.)

## Setup (one-time)

1. **Create a private GitHub repo** and push this folder to it.
2. **Deploy Postiz** (already provisioned on Azure for this project):
   - Runs via `docker compose` on an Ubuntu VM (`rg-postiz` / `postiz-vm`),
     fronted by Caddy for automatic HTTPS at a `sslip.io` domain tied to the
     VM's static IP (swap in a custom domain like `post.layer8culture.io` later
     by pointing an A record at the IP and editing `/opt/postiz/Caddyfile`).
   - **Register** the first account in the Postiz UI (it becomes admin), then
     **create an API key** under Settings → API.
   - **Instagram requires a Meta app** (Instagram Graph API): put its
     `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET` (and/or `FACEBOOK_APP_ID` /
     `FACEBOOK_APP_SECRET`) in `/opt/postiz/social.env` on the VM, then
     `sudo docker compose up -d`. Connection details are kept out of git in the
     session's `files/postiz-deployment.md`.
3. **Add GitHub repo secrets** (Settings → Secrets → Actions):
   | Secret | What it is |
   |---|---|
   | `COPILOT_CLI_TOKEN` | A token the Copilot CLI accepts for authentication. A **fine-grained** PAT with the **Copilot Requests** permission works only if your account/enterprise policy allows PAT-based Copilot access. If that's blocked, use the OAuth token the CLI stores after you run `copilot` → `/login` with your Copilot-seat account (a `gho_` token; classic `ghp_` PATs are NOT supported). |
   | `ENGINE_PR_TOKEN` | A user PAT with `repo` scope, used **only** to open the nightly approval PR. Required because the org disables "Allow GitHub Actions to create pull requests", so the built-in `GITHUB_TOKEN` can't create it — a PR authored by a real user can. |
   | `OPENAI_API_KEY` | From platform.openai.com (image generation) |
   | `POSTIZ_URL` | e.g. `https://post.layer8culture.io` |
   | `POSTIZ_API_KEY` | From your Postiz instance |
   | `LOFI_IG_CHANNEL_ID` | **Optional override.** The Layer8Culture Radio Instagram channel is already wired in `post_to_postiz.py`; set this only to point lofi posts at a different Postiz integration ID (e.g. after re-connecting the channel). |
   | `IG_USER_ID` | Instagram **Business** account user id (for the insights loop) |
   | `IG_GRAPH_TOKEN` | Long-lived Instagram Graph API token with `instagram_basic`, `instagram_manage_insights`, `pages_read_engagement` (reuses your Meta app) |
   | `REPORT_WEBHOOK` | Discord/Slack webhook for the daily report (optional) |
4. **Map your Instagram channel** — in Postiz, connect the Layer8Culture
   Instagram account (an IG **Business/Creator** account linked to a Facebook
   Page), then open **Settings → API** to copy that channel's **integration ID**.
   Paste it into `scripts/post_to_postiz.py` → `INTEGRATIONS[("layer8culture",
   "instagram")]`, replacing `REPLACE_ME`. The **Layer8Culture Radio (lofi)**
   account is already wired in `INTEGRATIONS[("lofi", "instagram")]`; set the
   `LOFI_IG_CHANNEL_ID` secret only if you re-connect it and need to override the ID.
   The other entries stay
   `REPLACE_ME` while X / TikTok are paused — unmapped posts are skipped, not errored.
   Full walkthrough: [`docs/postiz-instagram-setup.md`](docs/postiz-instagram-setup.md).
   To schedule **YouTube** uploads manually from Postiz, see
   [`docs/postiz-youtube-setup.md`](docs/postiz-youtube-setup.md) (the engine doesn't
   auto-post to YouTube — it promotes YouTube sessions on Instagram instead).
5. **Customize the brand files** in `brand/` — this is what makes output sound like you.
6. **Enable the workflows** in the Actions tab.

## Weekly rhythm

| When | What you do | Time |
|---|---|---|
| After Tech Thursday | Drop transcript in `transcripts/`, update `calendar/topics.md` | 5 min |
| When a lofi session goes live | Nothing — the engine auto-pulls new YouTube uploads into "Now Live" promo posts. (Optional: hand-edit `calendar/topics-lofi.md` to steer.) | 0 min |
| Nightly (automatic) | Engine opens a PR with tomorrow's layer8culture posts + visuals | 0 min |
| Mon/Wed/Fri (automatic) | Engine opens a separate PR with Layer8Culture Radio (lofi) posts | 0 min |
| Each morning | Review PR(s) on GitHub mobile, edit, merge | 2-5 min |
| Each morning | Read the daily report | 1 min |

> **The approval PR shows a visual preview.** Instead of a plain text summary, the
> PR body renders each post Postiz-style — the image(s), the exact caption that will
> post, hashtags, format and schedule time — built from the queue by
> `scripts/build_pr_preview.py`. Images embed by the pushed commit SHA via
> `raw.githubusercontent.com` (immutable; relies on this repo being **public**), so
> you can approve confidently from GitHub mobile without opening "Files changed".

## Folder map

- `brand/` — canonical guidelines (brand-guidelines-v2.md for layer8culture;
  layer8culture_radio_brand_guidelines.md for the lofi brand) + operational digests
  (voice-layer8culture.md, voice-lofi.md, visual style, hashtags). Read every run.
- `calendar/topics.md` — weekly steering for layer8culture ("lean into X this week").
- `calendar/topics-lofi.md` — steering + "Now Live" video links for the lofi brand.
  The "Now Live" list is auto-refreshed from the Layer8CultureRadio YouTube channel
  by `scripts/fetch_youtube.py` at the start of each lofi run (no API key — public
  RSS feed; channel `UC0AQjSCaU9ByaU90XabBbHQ`).
- `transcripts/` — Tech Thursday transcripts (pillar content).
- `queue/` — generated posts awaiting approval (the PR contents). layer8culture uses
  `queue/<date>.json`; the lofi brand uses `queue/lofi-<date>.json`.
- `posted/` — archive of published posts (feeds the report + dedupe).
- `analytics/` — Instagram Insights pulled back in (`insights.json`,
  `followers.json`, `insights-digest.md`); the digest steers next-day generation.
- `assets/library/` — your existing branded graphics + optional lofi audio bed
  (`lofi-bed.mp3`) and Tech Thursday recordings (for clip Reels).
- `assets/generated/` — OpenAI/ffmpeg outputs, named by post ID (images, carousel
  slides, reel mp4s + covers).
- `scripts/` — the machinery (`openai_gen.py` images, `reel_gen.py` video,
  `post_to_postiz.py` publishing, `list_postiz_channels.py` channel-ID lookup,
  `fetch_youtube.py` YouTube→"Now Live" refresh, `fetch_insights.py` analytics) +
  generation prompts (`generation-prompt.md` for layer8culture,
  `generation-prompt-lofi.md` for the lofi brand).
  `.github/workflows/` — the schedule (generate, generate-lofi, publish, analytics,
  daily-report).
