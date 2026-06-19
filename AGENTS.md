# Layer8Culture Content Engine — Agent Instructions

You are working inside the Layer8Culture content engine repository. This repo
generates, approves, and publishes social media content for the Layer8Culture
brand (layer8culture.io), its Tech Thursday live show, and its 24/7
Afrofuturistic LoFi stream.

## Always
- Treat brand/brand-guidelines-v2.md as the canonical source for the layer8culture
  brand, and brand/layer8culture_radio_brand_guidelines.md as the canonical source
  for the lofi brand (Layer8Culture Radio). The digest files (voice-layer8culture.md,
  voice-lofi.md, visual-style.md, hashtags.md) are the fast-path versions — keep them
  consistent with the relevant canonical doc.
- Core line: "Technology has seven layers. We're the eighth."
- Voice: calm confidence — cinematic, human, builder-first, culturally aware,
  premium. Never generic startup, hype, or fake motivational language.
- Every layer8culture post maps to exactly one of the 10 content categories in
  voice-layer8culture.md; every lofi post maps to one Layer8Culture Radio post type
  in voice-lofi.md (brand-intro / video-promo / quote / loop-reel / behind-the-scenes
  / playlist / community).
- Image prompts are ALWAYS composed per brand/visual-style.md
  (base prompt + add-on + scene + negative prompt). Lofi uses the LOFI BASE PROMPT,
  LOFI COLOR SYSTEM, and LOFI POST-TYPE notes — never the main BASE PROMPT.

## Two pipelines (keep them separate)
- **layer8culture** (nightly, generate-content.yml): news-driven posts on THREE platforms
  in one run — Instagram (the core feed), TikTok (4-6 reach-first videos/day), and YouTube
  (1-2 Shorts/day); prompt scripts/generation-prompt.md; queue/<date>.json; steered by
  calendar/topics.md. TikTok posts are platform "tiktok", format "reel", video-only: a
  CROSS-POST reuses the day's IG reel (visual.source "reuse", visual.of "<ig-reel-id>" —
  reel_gen.py pass 2 copies that reel's mp4/cover to the post id, zero extra render), and
  DEDICATED videos render with Sora-2 like an IG reel. YouTube Shorts are platform
  "youtube", format "reel", always a CROSS-POST (visual.source "reuse") of the day's best
  rendered reel, with a required youtube_title. The TikTok / YouTube Postiz channels
  resolve from the TIKTOK_CHANNEL_ID / YOUTUBE_LAYER8_CHANNEL_ID secrets (unset -> those
  posts skipped, not errored). YouTube uploads land PRIVATE until the Google app is
  verified (flip in Studio).
- **lofi / Layer8Culture Radio** (Mon/Wed/Fri, generate-lofi.yml): evergreen
  focus-music content + conditional "Now Live" video promos; prompt
  scripts/generation-prompt-lofi.md; queue/lofi-<date>.json; steered by
  calendar/topics-lofi.md. account is "lofi"; posts are platform "instagram" plus, when a
  run has a loop-reel, ONE platform "youtube" Short cross-posting it (resolves from
  YOUTUBE_LOFI_CHANNEL_ID; lofi TikTok stays paused/provisioning-only). The lofi account
  has NO composited wordmark for now (openai_gen.py ACCOUNT_WORDMARK maps lofi->None);
  its Postiz IG channel resolves from the LOFI_IG_CHANNEL_ID secret.

## Repo conventions
- Generated posts go in queue/ — layer8culture as YYYY-MM-DD.json, lofi as
  lofi-YYYY-MM-DD.json (schema in the matching generation prompt). Each post has a
  "format": single | carousel | reel | story. Published posts archive to posted/.
  publish.yml globs all queue/*.json on merge, so both prefixes publish automatically.
- Media outputs land in assets/generated/, named by post ID: <id>.png (single/
  story), <id>-N.png (carousel slides), <id>.mp4 + <id>-cover.png (reels).
- Reels are rendered by scripts/reel_gen.py with ffmpeg (a CI system tool, not a
  Python dep); it animates the still from openai_gen.py.
- The approval PR body is an auto-built visual preview (image(s) + exact caption +
  hashtags + schedule per post), produced by scripts/build_pr_preview.py and wired
  into generate-content.yml and generate-lofi.yml (gh pr create --body-file; the lofi
  run passes --brand "Layer8CultureRadio" for the title). It is stdlib-only and embeds
  images by the pushed commit SHA via raw.githubusercontent.com (needs a public repo).
  queue/<...>.summary.md is the short intro narrative shown above the per-post preview.
- Performance data lives in analytics/ (insights.json, followers.json,
  insights-digest.md), produced by scripts/fetch_insights.py from Instagram
  Insights. The digest steers generation — keep it in the prompt's READ FIRST list.
- File naming for brand assets follows Section 27 of the guidelines.
- Don't edit posted/log.json history; only append.

## When asked to modify the engine itself
- Keep scripts dependency-light (Python: openai + requests + pillow only; ffmpeg is
  a system tool for video).
- Workflow changes must preserve the human approval gate: nothing publishes
  without a merged PR.
