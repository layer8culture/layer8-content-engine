#!/usr/bin/env python3
"""Normalize Google Search Console CSV exports for AIGraph KPI tracking."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path


FIELD_ALIASES = {
    "date": {"date", "day"},
    "page": {"page", "pages", "url", "landing page", "top pages"},
    "query": {"query", "queries", "search query", "top queries"},
    "clicks": {"clicks"},
    "impressions": {"impressions"},
    "ctr": {"ctr"},
    "position": {"position", "average position"},
}


def normalize_key(key: str) -> str:
    return key.strip().lower().replace("\ufeff", "")


def find_field(headers: list[str], target: str) -> str | None:
    normalized = {normalize_key(header): header for header in headers}
    for alias in FIELD_ALIASES[target]:
        if alias in normalized:
            return normalized[alias]
    return None


def number(value: str) -> float:
    cleaned = value.strip().replace("%", "")
    if not cleaned:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def dimension_for_path(path: Path) -> str:
    name = path.name.lower()
    if "chart" in name:
        return "chart"
    if "quer" in name:
        return "query"
    if "page" in name:
        return "page"
    if "countr" in name:
        return "country"
    if "device" in name:
        return "device"
    if "appearance" in name:
        return "search_appearance"
    return "unknown"


def import_csv(path: Path) -> list[dict[str, object]]:
    dimension = dimension_for_path(path)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = reader.fieldnames or []
        fields = {name: find_field(headers, name) for name in FIELD_ALIASES}
        rows: list[dict[str, object]] = []
        for row in reader:
            clicks = number(row.get(fields["clicks"] or "", ""))
            impressions = number(row.get(fields["impressions"] or "", ""))
            ctr_value = number(row.get(fields["ctr"] or "", ""))
            if ctr_value > 1:
                ctr_value = ctr_value / 100
            if not ctr_value and impressions:
                ctr_value = clicks / impressions
            rows.append(
                {
                    "date": row.get(fields["date"] or "", ""),
                    "page": row.get(fields["page"] or "", ""),
                    "query": row.get(fields["query"] or "", ""),
                    "clicks": clicks,
                    "impressions": impressions,
                    "ctr": ctr_value,
                    "position": number(row.get(fields["position"] or "", "")),
                    "source_file": str(path),
                    "dimension": dimension,
                }
            )
        return rows


def summarize(rows: list[dict[str, object]]) -> dict[str, object]:
    aggregate_rows = [row for row in rows if row.get("dimension") == "chart"]
    if not aggregate_rows:
        aggregate_rows = [row for row in rows if row.get("dimension") == "page"]
    if not aggregate_rows:
        aggregate_rows = [row for row in rows if row.get("dimension") == "query"]
    clicks = sum(float(row["clicks"]) for row in aggregate_rows)
    impressions = sum(float(row["impressions"]) for row in aggregate_rows)
    aigraph_rows = [
        row
        for row in rows
        if "aigraph" in str(row.get("page", "")).lower()
        or "aigraph" in str(row.get("query", "")).lower()
        or "ai visibility" in str(row.get("query", "")).lower()
        or "answer engine" in str(row.get("query", "")).lower()
    ]
    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "rows": len(rows),
        "aggregate_dimension": aggregate_rows[0].get("dimension") if aggregate_rows else "none",
        "clicks": clicks,
        "impressions": impressions,
        "ctr": clicks / impressions if impressions else 0,
        "aigraph_rows": len(aigraph_rows),
        "aigraph_clicks": sum(float(row["clicks"]) for row in aigraph_rows),
        "aigraph_impressions": sum(float(row["impressions"]) for row in aigraph_rows),
    }


def write_markdown(summary: dict[str, object], rows: list[dict[str, object]], path: Path) -> None:
    top_rows = sorted(
        [row for row in rows if row.get("dimension") in {"query", "page"}],
        key=lambda row: float(row["impressions"]),
        reverse=True,
    )[:20]
    lines = [
        "# AIGraph Google Search Console KPI Import",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "## Summary",
        "",
        f"- Rows: {summary['rows']}",
        f"- Aggregate dimension: {summary['aggregate_dimension']}",
        f"- Clicks: {summary['clicks']:.0f}",
        f"- Impressions: {summary['impressions']:.0f}",
        f"- CTR: {summary['ctr']:.2%}",
        f"- AIGraph rows: {summary['aigraph_rows']}",
        f"- AIGraph clicks: {summary['aigraph_clicks']:.0f}",
        f"- AIGraph impressions: {summary['aigraph_impressions']:.0f}",
        "",
        "## Top rows by impressions",
        "",
        "| Dimension | Page | Query | Clicks | Impressions | CTR | Position |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for row in top_rows:
        lines.append(
            "| {dimension} | {page} | {query} | {clicks:.0f} | {impressions:.0f} | {ctr:.2%} | {position:.1f} |".format(
                dimension=str(row.get("dimension", "")),
                page=str(row.get("page", "")).replace("|", "/"),
                query=str(row.get("query", "")).replace("|", "/"),
                clicks=float(row["clicks"]),
                impressions=float(row["impressions"]),
                ctr=float(row["ctr"]),
                position=float(row["position"]),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Import Google Search Console CSV exports.")
    parser.add_argument("inputs", nargs="+", help="CSV export files from Google Search Console")
    parser.add_argument("--output-dir", default="analytics/aigraph")
    args = parser.parse_args()

    rows: list[dict[str, object]] = []
    for input_path in args.inputs:
        rows.extend(import_csv(Path(input_path)))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize(rows)
    (output_dir / "gsc-latest.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    write_markdown(summary, rows, output_dir / "gsc-latest.md")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
