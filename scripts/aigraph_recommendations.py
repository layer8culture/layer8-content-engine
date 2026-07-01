#!/usr/bin/env python3
"""Generate AIGraph recommendations from available audit artifacts."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def issue_recommendations(audit: dict[str, Any]) -> list[str]:
    issues = audit.get("issues", [])
    if not issues:
        return ["Maintain the current technical baseline. The latest AIGraph crawl audit reports zero issues."]
    recommendations = []
    if any("sitemap" in str(issue).lower() for issue in issues):
        recommendations.append("Fix sitemap delivery or XML validity before publishing more AIGraph pages.")
    if any("robots" in str(issue).lower() for issue in issues):
        recommendations.append("Fix robots.txt delivery before requesting additional indexing.")
    if any("canonical" in str(issue).lower() for issue in issues):
        recommendations.append("Add canonical links to pages missing canonical metadata.")
    if any("structured data" in str(issue).lower() or "json-ld" in str(issue).lower() for issue in issues):
        recommendations.append("Add JSON-LD structured data to pages missing schema.")
    if any("open graph" in str(issue).lower() or "twitter" in str(issue).lower() for issue in issues):
        recommendations.append("Add social metadata to pages missing Open Graph or Twitter card tags.")
    return recommendations or ["Review latest crawl audit issues and resolve the highest-impact technical blockers."]


def prompt_recommendations(prompt: dict[str, Any]) -> list[str]:
    summary = prompt.get("summary", {})
    recommendations: list[str] = []
    mention_rate = float(summary.get("mention_rate", 0) or 0)
    citation_rate = float(summary.get("citation_rate", 0) or 0)
    group_summary = summary.get("group_summary", {})
    if mention_rate < 0.4:
        recommendations.append("Increase AIGraph entity clarity and external mentions. Current prompt mention rate is below 40%.")
    if citation_rate < 0.25:
        recommendations.append("Earn and create more citation-worthy sources. Current prompt citation rate is below 25%.")
    for group in ("category", "informational", "niche"):
        item = group_summary.get(group, {})
        if float(item.get("mention_rate", 0) or 0) == 0:
            recommendations.append(f"Create or expand content for {group} prompts, where AIGraph currently has no proxy visibility.")
    competitors = [name for name, _ in summary.get("competitors", [])[:5]]
    if competitors:
        recommendations.append("Build comparison and alternative pages against dominant competitors: " + ", ".join(competitors) + ".")
    return recommendations


def gsc_recommendations(gsc: dict[str, Any]) -> list[str]:
    summary = gsc.get("summary", {})
    recommendations = []
    if not summary:
        return ["Import Search Console exports after indexing so AIGraph impressions and clicks can be trended."]
    aigraph_impressions = float(summary.get("aigraph_impressions", 0) or 0)
    if aigraph_impressions == 0:
        recommendations.append("Re-export Search Console after indexing. AIGraph impressions are still at zero in the current baseline.")
    if float(summary.get("impressions", 0) or 0) < 100:
        recommendations.append("Publish additional buyer-intent AIGraph resources to expand non-brand impressions.")
    return recommendations


def lighthouse_recommendations(lighthouse: dict[str, Any]) -> list[str]:
    scores = lighthouse.get("scores", {})
    recommendations = []
    if not scores:
        return ["Run Lighthouse monthly for performance, accessibility, best practices, and SEO drift."]
    if scores.get("performance", 100) < 90:
        recommendations.append("Improve performance. Lighthouse performance is below 90.")
    if scores.get("accessibility", 100) < 95:
        recommendations.append("Fix accessibility regressions. Lighthouse accessibility is below 95.")
    if scores.get("seo", 100) < 100:
        recommendations.append("Review Lighthouse SEO findings and fix regressions.")
    if scores.get("best-practices", 100) < 80:
        recommendations.append("Review best-practice warnings. Current known warnings are related to third-party analytics scripts and cookies.")
    return recommendations


def write_markdown(data: dict[str, Any], path: Path) -> None:
    lines = [
        "# AIGraph Weekly Recommendations",
        "",
        f"Generated: {data['generated_at']}",
        "",
        "## Priority recommendations",
        "",
    ]
    for index, item in enumerate(data["priority"], start=1):
        lines.append(f"{index}. {item}")
    lines.extend(["", "## Source signals", ""])
    for section, items in data["sections"].items():
        lines.extend([f"### {section}", ""])
        for item in items:
            lines.append(f"- {item}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate AIGraph recommendation report.")
    parser.add_argument("--input-dir", default="analytics/aigraph")
    parser.add_argument("--output-dir", default="analytics/aigraph")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    audit = load_json(input_dir / "latest.json")
    prompt = load_json(input_dir / "prompt-baseline-latest.json")
    gsc = load_json(input_dir / "gsc-latest.json")
    lighthouse = load_json(input_dir / "lighthouse" / "aigraph-lighthouse-summary.json")

    sections = {
        "technical_audit": issue_recommendations(audit),
        "prompt_visibility": prompt_recommendations(prompt),
        "search_console": gsc_recommendations(gsc),
        "lighthouse": lighthouse_recommendations(lighthouse),
    }
    priority = []
    for key in ("technical_audit", "prompt_visibility", "search_console", "lighthouse"):
        for item in sections[key]:
            if item not in priority:
                priority.append(item)

    report = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "sections": sections,
        "priority": priority[:12],
    }
    (output_dir / "recommendations-latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(report, output_dir / "recommendations-latest.md")
    print(json.dumps({"recommendations": len(report["priority"]), "output_dir": str(output_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
