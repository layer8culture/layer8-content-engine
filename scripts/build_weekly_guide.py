#!/usr/bin/env python3
"""Build a weekly Layer8Culture AI field guide and matching carousel queue."""
from __future__ import annotations

import argparse
import html
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


CAMPAIGNS_PATH = Path("campaigns/layer8culture-weekly-guides.json")
RESEARCH_PATH = Path("analytics/weekly-guide-research.json")
SITE_ROOT = Path("site")
QUEUE_ROOT = Path("queue")
BASE_URL = "https://www.layer8culture.io"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def eastern_today() -> datetime:
    return datetime.now(ZoneInfo("America/New_York"))


def friday_for_week(now: datetime) -> datetime:
    days_ahead = (4 - now.weekday()) % 7
    if days_ahead == 0 and now.hour >= 12:
        days_ahead = 7
    return now + timedelta(days=days_ahead)


def iso_with_offset(dt: datetime, hour: int = 11) -> str:
    scheduled = dt.replace(hour=hour, minute=0, second=0, microsecond=0)
    return scheduled.isoformat(timespec="seconds")


def choose_campaign(payload: dict, index: int | None = None) -> dict:
    campaigns = payload["campaigns"]
    if index is None:
        week_number = eastern_today().isocalendar().week
        index = (week_number - 1) % len(campaigns)
    return campaigns[index % len(campaigns)]


def source_links(research: dict, limit: int = 4) -> list[dict]:
    sources = [s for s in research.get("sources", []) if s.get("status") == "ok"]
    return sources[:limit]


def repo_lookup(research: dict) -> dict:
    return {
        repo["full_name"]: repo
        for repo in research.get("github_repos", [])
        if repo.get("status") == "ok" and repo.get("full_name")
    }


def list_html(items: list[str]) -> str:
    return "\n".join(f"<li>{html.escape(item)}</li>" for item in items)


def cards_html(cards: list[dict]) -> str:
    if not cards:
        return ""
    return '<div class="mini-grid">' + "\n".join(
        (
            '<article class="mini-card">'
            f'<h3>{html.escape(card["label"])}</h3>'
            f'<p>{html.escape(card["text"])}</p>'
            "</article>"
        )
        for card in cards
    ) + "</div>"


def section_html(section: dict) -> str:
    body = f'<p>{html.escape(section["body"])}</p>' if section.get("body") else ""
    bullets = ""
    if section.get("bullets"):
        bullets = f"<ul>{list_html(section['bullets'])}</ul>"
    checklist = ""
    if section.get("checklist"):
        checklist = '<ul class="checklist">' + list_html(section["checklist"]) + "</ul>"
    return f"""
    <section class="section">
      <div class="eyebrow">{html.escape(section["eyebrow"])}</div>
      <h2>{html.escape(section["heading"])}</h2>
      {body}
      {cards_html(section.get("cards", []))}
      {bullets}
      {checklist}
    </section>"""


def repo_cards_html(campaign: dict, research: dict) -> str:
    guide = campaign.get("guide", {})
    repos = guide.get("repo_reading_list", [])
    if not repos:
        return ""
    lookup = repo_lookup(research)
    cards = []
    for item in repos:
        full_name = item["repo"]
        meta = lookup.get(full_name, {})
        stars = meta.get("stars")
        stars_label = f"{stars:,} stars" if isinstance(stars, int) and stars > 0 else "Repo reference"
        language = meta.get("language") or "Mixed"
        url = meta.get("url") or f"https://github.com/{full_name}"
        lesson = item.get("why") or meta.get("lesson") or "Use this repo as a practical pattern source."
        cards.append(
            (
                '<article class="repo-card">'
                f'<a href="{html.escape(url)}">{html.escape(full_name)}</a>'
                f'<div class="repo-meta">{html.escape(stars_label)} | {html.escape(language)}</div>'
                f'<p>{html.escape(lesson)}</p>'
                "</article>"
            )
        )
    return "\n".join(cards)


def source_cards_html(research: dict) -> str:
    cards = []
    for src in source_links(research):
        cards.append(
            (
                '<article class="source-card">'
                f'<a href="{html.escape(src["url"])}">{html.escape(src["name"])}</a>'
                f'<p>{html.escape(src.get("summary", ""))}</p>'
                "</article>"
            )
        )
    return "\n".join(cards)


def campaign_source_cards_html(campaign: dict, research: dict) -> str:
    source_notes = campaign.get("guide", {}).get("source_notes", [])
    if not source_notes:
        return source_cards_html(research)
    return "\n".join(
        (
            '<article class="source-card">'
            f'<a href="{html.escape(src["url"])}">{html.escape(src["name"])}</a>'
            f'<p>{html.escape(src["note"])}</p>'
            "</article>"
        )
        for src in source_notes
    )


def glossary_html(campaign: dict) -> str:
    glossary = campaign.get("guide", {}).get("glossary", [])
    if not glossary:
        return ""
    items = "\n".join(
        (
            '<div class="glossary-item">'
            f'<dt>{html.escape(term)}</dt>'
            f'<dd>{html.escape(definition)}</dd>'
            "</div>"
        )
        for term, definition in glossary
    )
    return f"""
    <section class="section">
      <div class="eyebrow">Glossary</div>
      <h2>Keep these terms straight</h2>
      <dl class="glossary">{items}</dl>
    </section>"""


def fallback_sections(campaign: dict, research: dict) -> str:
    steps = "\n".join(
        f"<li><strong>{html.escape(headline)}.</strong> {html.escape(subtext)}</li>"
        for headline, subtext in campaign["slides"][1:-1]
    )
    recommendations = "\n".join(
        f"<li>{html.escape(item)}</li>" for item in research.get("recommendations", [])
    )
    return f"""
    <section class="section">
      <div class="eyebrow">Framework</div>
      <h2>The short version</h2>
      <ul>{steps}</ul>
    </section>

    <section class="section">
      <div class="eyebrow">Builder checklist</div>
      <h2>Use this this week</h2>
      <ul>
        <li>Pick one real task you already do every week.</li>
        <li>Write the outcome before choosing the AI tool.</li>
        <li>Give the model context, constraints, and a review standard.</li>
        <li>Connect a tool only when it removes repeated copy-paste.</li>
        <li>Save the workflow once it works twice.</li>
      </ul>
    </section>

    <section class="section">
      <div class="eyebrow">Research notes</div>
      <h2>Why this topic now</h2>
      <ul>{recommendations}</ul>
    </section>"""


def render_guide(campaign: dict, research: dict) -> str:
    title = html.escape(campaign["title"])
    hook = html.escape(campaign["hook"])
    guide = campaign.get("guide", {})
    promise = html.escape(guide.get("hero_promise", campaign["guide_promise"]))
    keyword = html.escape(campaign["keyword"])
    read_time = html.escape(guide.get("read_time", "Field guide"))
    updated_label = html.escape(guide.get("updated_label", "Public guide"))
    summary_cards = cards_html(guide.get("summary_cards", []))
    if not summary_cards:
        summary_cards = cards_html(
            [
                {"label": "What this solves", "text": campaign["angle"]},
                {
                    "label": "Who it is for",
                    "text": "Builders, creators, developers, and curious operators learning how to use AI with more clarity.",
                },
                {
                    "label": "The move",
                    "text": "Use the guide as a small weekly practice. Learn one idea, then apply it to one real workflow.",
                },
            ]
        )
    sections = "\n".join(section_html(section) for section in guide.get("sections", []))
    if not sections:
        sections = fallback_sections(campaign, research)
    starter_prompt = guide.get("starter_prompt", "")
    prompt_html = ""
    if starter_prompt:
        prompt_html = f"""
    <section class="section prompt-block">
      <div class="eyebrow">Starter prompt</div>
      <h2>Use this to plan your first workflow</h2>
      <p>{html.escape(starter_prompt)}</p>
    </section>"""
    repo_html = repo_cards_html(campaign, research)
    repo_section = ""
    if repo_html:
        repo_section = f"""
    <section class="section">
      <div class="eyebrow">GitHub repo patterns</div>
      <h2>Repos worth studying</h2>
      <div class="repo-grid">{repo_html}</div>
    </section>"""
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} | Layer8Culture</title>
  <meta name="description" content="{promise}">
  <link rel="stylesheet" href="/styles.css">
</head>
<body>
  <main class="shell">
    <nav class="nav">
      <a class="mark" href="/">Layer8Culture</a>
      <div>The Eighth Layer AI Field Guide</div>
    </nav>
    <section class="hero">
      <div class="eyebrow">AI Field Guide</div>
      <h1>{title}</h1>
      <p class="lede">{hook}</p>
      <p class="lede">{promise}</p>
      <div class="meta">
        <span class="pill">Keyword: {keyword}</span>
        <span class="pill">{html.escape(campaign["category"])}</span>
        <span class="pill">{read_time}</span>
        <span class="pill">{updated_label}</span>
      </div>
    </section>

    {summary_cards}

    {sections}
    {prompt_html}
    {glossary_html(campaign)}
    {repo_section}

    <section class="section sources">
      <div class="eyebrow">Sources</div>
      <h2>Read next</h2>
      <div class="source-grid">{campaign_source_cards_html(campaign, research)}</div>
    </section>

    <section class="cta">
      <div class="eyebrow">Layer8Culture</div>
      <h2>Technology has seven layers. We're the eighth.</h2>
      <p>Follow Layer8Culture for practical AI fluency, creator systems, and build-in-public experiments.</p>
    </section>
  </main>
  <footer class="footer">
    <div class="shell">Layer8Culture</div>
  </footer>
</body>
</html>
"""


def visual_prompt(campaign: dict, headline: str, index: int) -> str:
    scene = (
        "Create a cinematic Layer8Culture carousel slide in a deep black and deep navy "
        "environment with electric blue accents, premium creator-tech atmosphere, "
        "human-centered AI themes, strong negative space, and a distinct scene. "
        f"Topic: {campaign['title']}. Slide idea: {headline}. "
        "Use abstract tool-connected AI workflows, terminal windows as soft shapes, "
        "node graphs, premium studio details, and builder energy. No rendered text."
    )
    if index % 2 == 0:
        scene += " Make this slide feel like a macro detail of a workflow coming together."
    else:
        scene += " Make this slide feel like a wide cinematic system map in motion."
    return scene


def build_queue_post(campaign: dict, guide_url: str, schedule_time: str, post_id: str) -> dict:
    keyword = campaign["keyword"]
    caption = (
        f"{campaign['hook']}\n\n"
        f"This week's AI field guide is about {campaign['title']}.\n\n"
        "The carousel gives you the short version.\n"
        "The guide gives you the practical map.\n\n"
        f"Comment {keyword} and I will send you the full AI field guide."
    )
    slides = []
    for index, (headline, subtext) in enumerate(campaign["slides"], 1):
        slides.append(
            {
                "openai_prompt": visual_prompt(campaign, headline, index),
                "headline": headline,
                "subtext": subtext,
                "accent": headline.split()[0],
                "role": "cover" if index == 1 else "cta" if index == len(campaign["slides"]) else "value",
            }
        )
    return {
        "id": post_id,
        "account": "layer8culture",
        "category": campaign["category"],
        "platform": "instagram",
        "format": "carousel",
        "schedule_time": schedule_time,
        "text": caption,
        "hashtags": [
            "#Layer8Culture",
            "#AIFluency",
            "#BuildWithAI",
            "#AIBuilders",
            "#AItools",
            "#PromptEngineering",
            "#AIagents",
            "#BuildInPublic",
        ],
        "hashtags_in_first_comment": True,
        "comment_to_dm": {
            "campaign": f"weekly_guide_{campaign['slug'].replace('-', '_')}",
            "keyword": keyword,
            "offer": campaign["offer"],
            "guide_url": guide_url,
            "dm_prompt": (
                f"Here is the AI field guide: {guide_url} "
                "What are you trying to get better at with AI right now?"
            ),
            "public_reply": "Sent. Check your DMs.",
            "route": "ai_field_guide_nurture",
        },
        "visual": {
            "source": "openai",
            "aspect": "1:1",
            "quality": "high",
            "slides": slides,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-index", type=int)
    parser.add_argument("--date")
    args = parser.parse_args()

    campaigns = load_json(CAMPAIGNS_PATH)
    research = load_json(RESEARCH_PATH) if RESEARCH_PATH.exists() else {"recommendations": [], "sources": []}
    campaign = choose_campaign(campaigns, args.campaign_index)

    now = eastern_today()
    target = datetime.fromisoformat(args.date) if args.date else friday_for_week(now)
    if target.tzinfo is None:
        target = target.replace(tzinfo=ZoneInfo("America/New_York"))
    date = target.strftime("%Y-%m-%d")
    ymd = target.strftime("%Y%m%d")
    schedule_time = iso_with_offset(target)
    guide_url = f"{BASE_URL}/guides/{campaign['slug']}/"
    active_campaign = {
        "campaign": f"weekly_guide_{campaign['slug'].replace('-', '_')}",
        "keyword": campaign["keyword"],
        "offer": campaign["offer"],
        "guide_url": guide_url,
        "dm_prompt": (
            f"Here is the AI field guide: {guide_url} "
            "What are you trying to get better at with AI right now?"
        ),
        "public_reply": "Sent. Check your DMs.",
        "route": "ai_field_guide_nurture",
    }

    guide_dir = SITE_ROOT / "guides" / campaign["slug"]
    guide_dir.mkdir(parents=True, exist_ok=True)
    (guide_dir / "index.html").write_text(render_guide(campaign, research), encoding="utf-8")

    QUEUE_ROOT.mkdir(exist_ok=True)
    post = build_queue_post(
        campaign=campaign,
        guide_url=guide_url,
        schedule_time=schedule_time,
        post_id=f"{ymd}-layer8culture-instagram-guide",
    )
    queue_path = QUEUE_ROOT / f"weekly-guide-{date}.json"
    queue_path.write_text(json.dumps([post], indent=2), encoding="utf-8")

    summary = (
        f"# Weekly AI Field Guide - {date}\n\n"
        f"- **Carousel** - {campaign['category']} - `{post['id']}`\n"
        f"- **Guide:** {guide_url}\n"
        f"- **Keyword:** `{campaign['keyword']}`\n"
        f"- **Hook:** {campaign['hook']}\n"
        f"- **CTA:** Comment {campaign['keyword']} and I will send you the full AI field guide.\n"
    )
    (QUEUE_ROOT / f"weekly-guide-{date}.summary.md").write_text(summary, encoding="utf-8")
    active_path = Path("campaigns/layer8culture-active-guide-campaign.json")
    active_path.write_text(json.dumps(active_campaign, indent=2), encoding="utf-8")
    print(f"Wrote {guide_dir / 'index.html'}")
    print(f"Wrote {queue_path}")
    print(f"Wrote {active_path}")


if __name__ == "__main__":
    main()
