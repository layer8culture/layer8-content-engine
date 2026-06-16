# Layer8Culture Content Engine — Agent Instructions

You are working inside the Layer8Culture content engine repository. This repo
generates, approves, and publishes social media content for the Layer8Culture
brand (layer8culture.io), its Tech Thursday live show, and its 24/7
Afrofuturistic LoFi stream.

## Always
- Treat brand/brand-guidelines-v2.md as the canonical brand source. The digest
  files (voice-layer8culture.md, voice-lofi.md, visual-style.md, hashtags.md)
  are the fast-path versions — keep them consistent with the canonical doc.
- Core line: "Technology has seven layers. We're the eighth."
- Voice: calm confidence — cinematic, human, builder-first, culturally aware,
  premium. Never generic startup, hype, or fake motivational language.
- Every social post maps to exactly one of the 10 content categories in
  voice-layer8culture.md.
- Image prompts are ALWAYS composed per brand/visual-style.md
  (base prompt + add-on + scene + negative prompt).

## Repo conventions
- Generated posts go in queue/ as YYYY-MM-DD.json (schema in
  scripts/generation-prompt.md). Each post has a "format": single | carousel |
  reel | story. Published posts archive to posted/.
- Media outputs land in assets/generated/, named by post ID: <id>.png (single/
  story), <id>-N.png (carousel slides), <id>.mp4 + <id>-cover.png (reels).
- Reels are rendered by scripts/reel_gen.py with ffmpeg (a CI system tool, not a
  Python dep); it animates the still from openai_gen.py.
- The approval PR body is an auto-built visual preview (image(s) + exact caption +
  hashtags + schedule per post), produced by scripts/build_pr_preview.py and wired
  into generate-content.yml (gh pr create --body-file). It is stdlib-only and embeds
  images by the pushed commit SHA via raw.githubusercontent.com (needs a public repo).
  queue/<date>.summary.md is the short intro narrative shown above the per-post preview.
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
