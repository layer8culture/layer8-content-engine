#!/usr/bin/env python3
"""Create and summarize manual AI visibility prompt baselines."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def summarize(data: dict[str, Any]) -> dict[str, Any]:
    business = data.get("business", {})
    name = str(business.get("name", "")).lower()
    offer = str(business.get("offer", "")).lower()
    results = data.get("results", [])
    prompt_count = len(data.get("prompts", []))
    engine_count = len(data.get("engines", []))
    expected_runs = prompt_count * engine_count
    group_counts: dict[str, Counter[str]] = defaultdict(Counter)
    mentions = 0
    citations = 0
    inaccurate = 0
    competitors = Counter()

    for item in results:
        group = str(item.get("group", "unknown"))
        mentioned = bool(item.get("business_mentioned"))
        cited = bool(item.get("business_cited"))
        accurate = item.get("answer_accuracy", "unknown")
        if mentioned:
            mentions += 1
            group_counts[group]["mentions"] += 1
        if cited:
            citations += 1
            group_counts[group]["citations"] += 1
        if str(accurate).lower() in {"low", "inaccurate", "wrong", "false"}:
            inaccurate += 1
        for competitor in item.get("competitors_mentioned", []) or []:
            competitors[str(competitor)] += 1
        group_counts[group]["runs"] += 1

    completed = len(results)
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "business": business,
        "prompt_count": prompt_count,
        "engine_count": engine_count,
        "expected_runs": expected_runs,
        "completed_runs": completed,
        "completion_rate": completed / expected_runs if expected_runs else 0,
        "mention_rate": mentions / completed if completed else 0,
        "citation_rate": citations / completed if completed else 0,
        "inaccurate_count": inaccurate,
        "group_summary": {
            group: {
                "runs": counts["runs"],
                "mention_rate": counts["mentions"] / counts["runs"] if counts["runs"] else 0,
                "citation_rate": counts["citations"] / counts["runs"] if counts["runs"] else 0,
            }
            for group, counts in sorted(group_counts.items())
        },
        "competitors": competitors.most_common(),
        "search_terms": [term for term in [name, offer] if term],
    }


def write_markdown(summary: dict[str, Any], data: dict[str, Any], path: Path) -> None:
    lines = [
        "# AIGraph AI Visibility Prompt Baseline",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "## Summary",
        "",
        f"- Business: {summary['business'].get('name', '')}",
        f"- Expected runs: {summary['expected_runs']}",
        f"- Completed runs: {summary['completed_runs']}",
        f"- Completion rate: {summary['completion_rate']:.1%}",
        f"- Mention rate: {summary['mention_rate']:.1%}",
        f"- Citation rate: {summary['citation_rate']:.1%}",
        f"- Inaccurate answer count: {summary['inaccurate_count']}",
        "",
        "## Prompt group summary",
        "",
        "| Group | Runs | Mention rate | Citation rate |",
        "|---|---:|---:|---:|",
    ]
    for group, item in summary["group_summary"].items():
        lines.append(f"| {group} | {item['runs']} | {item['mention_rate']:.1%} | {item['citation_rate']:.1%} |")
    lines.extend(["", "## Competitors mentioned", ""])
    if summary["competitors"]:
        for competitor, count in summary["competitors"]:
            lines.append(f"- {competitor}: {count}")
    else:
        lines.append("- No competitor mentions recorded yet.")
    lines.extend(["", "## How to fill results", ""])
    lines.append("For each prompt and engine, add an object to `results` with these fields:")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps({
        "group": "category",
        "engine": "ChatGPT",
        "prompt": "What companies help businesses track AI search visibility?",
        "business_mentioned": False,
        "business_cited": False,
        "answer_accuracy": "unknown",
        "citations": [],
        "competitors_mentioned": [],
        "notes": ""
    }, indent=2))
    lines.append("```")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize an AIGraph AI visibility prompt baseline.")
    parser.add_argument("--input", default="analytics/aigraph/prompt-baseline-template.json")
    parser.add_argument("--output-dir", default="analytics/aigraph")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    data = load(input_path)
    summary = summarize(data)
    (output_dir / "prompt-baseline-latest.json").write_text(json.dumps({"summary": summary, "data": data}, indent=2), encoding="utf-8")
    write_markdown(summary, data, output_dir / "prompt-baseline-latest.md")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
