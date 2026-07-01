#!/usr/bin/env python3
"""Run a small public SEO and AEO readiness audit for Layer8Culture."""

from __future__ import annotations

import argparse
import datetime as dt
import html.parser
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_PATHS = [
    "/",
    "/aigraph/",
    "/aigraph/ai-visibility-audit/",
    "/aigraph/monthly-monitoring/",
    "/aigraph/case-studies/",
    "/aigraph/case-studies/layer8culture-baseline/",
    "/aigraph/resources/ai-visibility-checklist/",
    "/guides/mcp-explained-without-the-hype/",
    "/robots.txt",
    "/sitemap.xml",
    "/llms.txt",
]


class MetadataParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.in_h1 = False
        self.title_parts: list[str] = []
        self.h1: list[str] = []
        self.h2: list[str] = []
        self.current_h2 = False
        self.meta: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.anchors: list[str] = []
        self.structured_data_count = 0
        self.current_script_ld = False
        self.structured_data: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k.lower(): v or "" for k, v in attrs}
        if tag == "title":
            self.in_title = True
        elif tag == "h1":
            self.in_h1 = True
        elif tag == "h2":
            self.current_h2 = True
        elif tag == "meta":
            self.meta.append(attr)
        elif tag == "link":
            self.links.append(attr)
        elif tag == "a" and attr.get("href"):
            self.anchors.append(attr["href"])
        elif tag == "script" and attr.get("type", "").lower() == "application/ld+json":
            self.current_script_ld = True
            self.structured_data_count += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        elif tag == "h1":
            self.in_h1 = False
        elif tag == "h2":
            self.current_h2 = False
        elif tag == "script":
            self.current_script_ld = False

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self.in_title:
            self.title_parts.append(text)
        elif self.in_h1:
            self.h1.append(text)
        elif self.current_h2:
            self.h2.append(text)
        elif self.current_script_ld:
            self.structured_data.append(data.strip())


def fetch(url: str) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "AIGraphAudit/1.0 (+https://www.layer8culture.io/aigraph/)"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
            return {
                "url": url,
                "status": response.status,
                "final_url": response.geturl(),
                "content_type": response.headers.get("Content-Type", ""),
                "cache_control": response.headers.get("Cache-Control", ""),
                "bytes": len(body),
                "body": body.decode("utf-8", errors="replace"),
                "error": "",
            }
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return {
            "url": url,
            "status": exc.code,
            "final_url": exc.geturl(),
            "content_type": exc.headers.get("Content-Type", ""),
            "cache_control": exc.headers.get("Cache-Control", ""),
            "bytes": len(body),
            "body": body.decode("utf-8", errors="replace"),
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "url": url,
            "status": 0,
            "final_url": url,
            "content_type": "",
            "cache_control": "",
            "bytes": 0,
            "body": "",
            "error": str(exc),
        }


def meta_value(parser: MetadataParser, key: str, value: str, content_key: str = "content") -> str:
    for item in parser.meta:
        if item.get(key) == value:
            return item.get(content_key, "")
    return ""


def link_value(parser: MetadataParser, rel: str) -> str:
    for item in parser.links:
        if item.get("rel", "").lower() == rel:
            return item.get("href", "")
    return ""


def analyze_page(result: dict[str, object]) -> dict[str, object]:
    body = str(result["body"])
    content_type = str(result["content_type"])
    page: dict[str, object] = {
        "url": result["url"],
        "status": result["status"],
        "final_url": result["final_url"],
        "content_type": content_type,
        "bytes": result["bytes"],
        "error": result["error"],
    }
    if "text/html" in content_type or body.lstrip().lower().startswith("<!doctype html"):
        parser = MetadataParser()
        parser.feed(body)
        title = " ".join(parser.title_parts).strip()
        description = meta_value(parser, "name", "description")
        canonical = link_value(parser, "canonical")
        og_title = meta_value(parser, "property", "og:title")
        twitter_title = meta_value(parser, "name", "twitter:title")
        internal_links = [
            href
            for href in parser.anchors
            if href.startswith("/") or href.startswith("https://www.layer8culture.io")
        ]
        page.update(
            {
                "title": title,
                "title_length": len(title),
                "description": description,
                "description_length": len(description),
                "canonical": canonical,
                "h1_count": len(parser.h1),
                "h1": parser.h1,
                "h2_count": len(parser.h2),
                "structured_data_count": parser.structured_data_count,
                "og_title_present": bool(og_title),
                "twitter_title_present": bool(twitter_title),
                "internal_link_count": len(internal_links),
                "internal_links": sorted(set(internal_links)),
            }
        )
    else:
        page.update(
            {
                "title": "",
                "title_length": 0,
                "description": "",
                "description_length": 0,
                "canonical": "",
                "h1_count": 0,
                "h1": [],
                "h2_count": 0,
                "structured_data_count": 0,
                "og_title_present": False,
                "twitter_title_present": False,
                "internal_link_count": 0,
                "internal_links": [],
            }
        )
    return page


def score_page(page: dict[str, object]) -> list[str]:
    issues: list[str] = []
    url = str(page["url"])
    if int(page["status"]) != 200:
        issues.append(f"{url}: status is {page['status']}")
    if url.endswith("/robots.txt"):
        body_type = str(page["content_type"])
        if "text/plain" not in body_type and "text/html" in body_type:
            issues.append(f"{url}: robots.txt is served as HTML")
    if url.endswith("/sitemap.xml"):
        body_type = str(page["content_type"])
        if "xml" not in body_type:
            issues.append(f"{url}: sitemap.xml is not served as XML")
    if "text/html" in str(page["content_type"]):
        if not page.get("title"):
            issues.append(f"{url}: missing title")
        if not page.get("description"):
            issues.append(f"{url}: missing meta description")
        if not page.get("canonical"):
            issues.append(f"{url}: missing canonical link")
        if int(page.get("h1_count", 0)) != 1:
            issues.append(f"{url}: expected exactly one H1")
        if int(page.get("structured_data_count", 0)) == 0:
            issues.append(f"{url}: missing JSON-LD structured data")
        if not page.get("og_title_present"):
            issues.append(f"{url}: missing Open Graph title")
        if not page.get("twitter_title_present"):
            issues.append(f"{url}: missing Twitter title")
    return issues


def write_markdown(report: dict[str, object], path: Path) -> None:
    lines = [
        "# AIGraph SEO and AEO Baseline",
        "",
        f"Generated: {report['generated_at']}",
        f"Base URL: {report['base_url']}",
        "",
        "## Summary",
        "",
        f"- URLs checked: {len(report['pages'])}",
        f"- Issues found: {len(report['issues'])}",
        "",
        "## Issues",
        "",
    ]
    if report["issues"]:
        lines.extend(f"- {issue}" for issue in report["issues"])
    else:
        lines.append("- No issues found.")
    lines.extend(["", "## Pages", ""])
    lines.append("| URL | Status | Title | Description | Canonical | JSON-LD |")
    lines.append("|---|---:|---|---|---|---:|")
    for page in report["pages"]:
        lines.append(
            "| {url} | {status} | {title} | {description} | {canonical} | {sd} |".format(
                url=page["url"],
                status=page["status"],
                title=str(page.get("title", "")).replace("|", "/"),
                description=("yes" if page.get("description") else "no"),
                canonical=("yes" if page.get("canonical") else "no"),
                sd=page.get("structured_data_count", 0),
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run AIGraph SEO and AEO readiness checks.")
    parser.add_argument("--base-url", default="https://www.layer8culture.io")
    parser.add_argument("--output-dir", default="analytics/aigraph")
    parser.add_argument("--paths", nargs="*", default=DEFAULT_PATHS)
    args = parser.parse_args(argv)

    base = args.base_url.rstrip("/")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = dt.datetime.now(dt.timezone.utc).isoformat()
    pages = []
    issues: list[str] = []
    for raw_path in args.paths:
        if raw_path.startswith("http"):
            url = raw_path
        else:
            url = base + "/" + raw_path.lstrip("/")
        result = fetch(url)
        page = analyze_page(result)
        pages.append(page)
        issues.extend(score_page(page))

    report = {
        "generated_at": generated_at,
        "base_url": base,
        "pages": pages,
        "issues": issues,
    }
    (output_dir / "latest.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_markdown(report, output_dir / "latest.md")
    print(json.dumps({"pages": len(pages), "issues": len(issues), "output_dir": str(output_dir)}, indent=2))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
