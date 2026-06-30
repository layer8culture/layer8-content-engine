# Weekly AI Field Guide Campaign

Layer8Culture now has a weekly Instagram carousel plus public web guide campaign.
The goal is practical AI education: helpful AI tips, skills, MCP, AI fluency, and
using AI tools well.

## What it creates

- One `layer8culture` Instagram carousel per week.
- One public guide page under `site/guides/<slug>/index.html`.
- One queue file named `queue/weekly-guide-YYYY-MM-DD.json`.
- One `comment_to_dm` block with a keyword and public guide URL.
- One active campaign export at `campaigns/layer8culture-active-guide-campaign.json`.

## Topic source

Campaign ideas live in:

```text
campaigns/layer8culture-weekly-guides.json
```

The current lanes are:

- Helpful AI tips
- AI skills
- MCP
- AI fluency
- Prompt and context habits
- Using AI tools well
- Agentic workflows

## Workflow

The weekly GitHub Action is:

```text
.github/workflows/generate-weekly-guide.yml
```

It runs the research scraper, builds the guide page and carousel queue item, generates
carousel visuals, and opens an approval PR.

After the PR is merged, the site deploy workflow publishes `site/` to the Azure Static
Web App behind `www.layer8culture.io`:

```text
.github/workflows/deploy-layer8-site.yml
```

The deploy secret is:

```text
AZURE_STATIC_WEB_APPS_API_TOKEN_LAYER8CULTURE
```

## Manual generation

```powershell
python scripts\research_weekly_guide.py
python scripts\build_weekly_guide.py --campaign-index 0 --date 2026-07-03
```

Then generate visuals for the queue file:

```powershell
python scripts\openai_gen.py queue\weekly-guide-2026-07-03.json
```

## n8n webhook

The active weekly guide campaign is copied into n8n so comment keywords can DM the
public guide link.

Current webhook callback:

```text
https://n8n.20-120-36-39.sslip.io/webhook/meta-instagram-comments
```

The guide page is public, so the DM is a convenience and a conversation starter, not
the only way to access the resource.
