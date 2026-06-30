#!/usr/bin/env python3
"""Fetch lightweight web research for Layer8Culture weekly AI field guides."""
from __future__ import annotations

import argparse
import json
import os
import re
import textwrap
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path


DEFAULT_SOURCES = [
    {
        "name": "Model Context Protocol Docs",
        "url": "https://modelcontextprotocol.io/docs/getting-started/intro",
        "topics": ["mcp", "architecture", "tools", "resources", "prompts"],
    },
    {
        "name": "MCP Example Servers",
        "url": "https://modelcontextprotocol.io/examples",
        "topics": ["mcp", "examples", "servers"],
    },
    {
        "name": "GitHub Blog - AI and ML",
        "url": "https://github.blog/ai-and-ml/",
        "topics": ["copilot", "agents", "developer workflows", "mcp"],
    },
    {
        "name": "GitHub Changelog - Copilot",
        "url": "https://github.blog/changelog/label/copilot/",
        "topics": ["copilot", "cli", "agentic workflows"],
    },
    {
        "name": "Microsoft Copilot Blog",
        "url": "https://www.microsoft.com/en-us/microsoft-copilot/blog/",
        "topics": ["agents", "skills", "workflow"],
    },
    {
        "name": "OpenAI News",
        "url": "https://openai.com/news/",
        "topics": ["tools", "models", "agents"],
    },
    {
        "name": "Anthropic News",
        "url": "https://www.anthropic.com/news",
        "topics": ["claude", "tools", "mcp"],
    },
]

DEFAULT_GITHUB_REPOS = [
    "modelcontextprotocol/servers",
    "modelcontextprotocol/typescript-sdk",
    "modelcontextprotocol/python-sdk",
    "modelcontextprotocol/inspector",
    "github/github-mcp-server",
    "punkpeye/awesome-mcp-servers",
    "modelcontextprotocol/registry",
    "hangwin/mcp-chrome",
    "executeautomation/mcp-playwright",
    "microsoft/mcp",
]

KEYWORDS = {
    "mcp": 6,
    "model context protocol": 8,
    "agent": 5,
    "copilot": 5,
    "cli": 4,
    "workflow": 5,
    "tools": 4,
    "skills": 4,
    "automation": 4,
    "prompt": 3,
    "developer": 3,
    "builder": 3,
}


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "svg"}:
            self._skip = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "svg"}:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            text = " ".join(data.split())
            if text:
                self.parts.append(text)


def fetch_text(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Layer8CultureContentEngine/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read(750_000)
    parser = TextExtractor()
    parser.feed(raw.decode("utf-8", errors="replace"))
    return " ".join(parser.parts)


def fetch_json(url: str, timeout: int = 20) -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Layer8CultureContentEngine/1.0",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def score_text(text: str) -> int:
    lower = text.lower()
    return sum(weight * lower.count(keyword) for keyword, weight in KEYWORDS.items())


def summarize(text: str, limit: int = 420) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return textwrap.shorten(cleaned, width=limit, placeholder="...")


def research_sources(sources: list[dict]) -> list[dict]:
    results = []
    for source in sources:
        url = source["url"]
        try:
            text = fetch_text(url)
            results.append(
                {
                    "name": source["name"],
                    "url": url,
                    "topics": source.get("topics", []),
                    "score": score_text(text),
                    "summary": summarize(text),
                    "status": "ok",
                }
            )
        except Exception as exc:
            results.append(
                {
                    "name": source["name"],
                    "url": url,
                    "topics": source.get("topics", []),
                    "score": 0,
                    "summary": str(exc),
                    "status": "error",
                }
            )
    return sorted(results, key=lambda item: item["score"], reverse=True)


def repo_lesson(full_name: str, description: str) -> str:
    lower = f"{full_name} {description}".lower()
    if "sdk" in lower:
        return "Use SDK examples to see the client and server primitives without extra product noise."
    if "inspector" in lower:
        return "Use inspection tools to test a server before trusting it inside a real workflow."
    if "github" in lower:
        return "Treat code hosting as a workflow surface for issues, pull requests, files, and reviews."
    if "playwright" in lower or "chrome" in lower:
        return "Browser automation is useful when the workflow needs pages, forms, screenshots, or live web context."
    if "registry" in lower or "awesome" in lower:
        return "Directories help builders compare the ecosystem, but every server still needs a security review."
    if "microsoft" in lower:
        return "Enterprise MCP examples show how tool access maps to data boundaries and governance."
    if "servers" in lower:
        return "Study reference servers to understand how tools, resources, prompts, and permissions fit together."
    return "Use this repo as a pattern source, then decide if the capability belongs in your workflow."


def github_repo_research(repos: list[str]) -> list[dict]:
    results = []
    for full_name in repos:
        try:
            data = fetch_json(f"https://api.github.com/repos/{full_name}")
            description = data.get("description") or ""
            results.append(
                {
                    "full_name": data["full_name"],
                    "url": data["html_url"],
                    "stars": data["stargazers_count"],
                    "language": data.get("language"),
                    "updated_at": data.get("updated_at"),
                    "description": description,
                    "topics": data.get("topics", []),
                    "lesson": repo_lesson(data["full_name"], description),
                    "status": "ok",
                }
            )
        except Exception as exc:
            results.append(
                {
                    "full_name": full_name,
                    "url": f"https://github.com/{full_name}",
                    "stars": 0,
                    "language": None,
                    "updated_at": None,
                    "description": str(exc),
                    "topics": [],
                    "lesson": "Could not fetch metadata. Review manually before citing.",
                    "status": "error",
                }
            )
    return sorted(results, key=lambda item: item.get("stars", 0), reverse=True)


def build_digest(results: list[dict], github_repos: list[dict]) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "focus": [
            "helpful AI tips",
            "AI skills",
            "MCP",
            "AI fluency",
            "using AI tools well",
            "agentic workflows",
        ],
        "recommendations": [
            "Prefer practical, saveable carousels with a clear skill or workflow lesson.",
            "Use contrast formats like do this, not that when the topic is crowded.",
            "Tie tool news back to the builder identity: consumer, user, builder, operator.",
            "Make the guide useful without requiring a private download.",
            "Use top GitHub repos as pattern evidence, not as hype badges.",
            "Always include a security and trust checklist for tool-connected AI content.",
        ],
        "sources": results,
        "github_repos": github_repos,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="analytics/weekly-guide-research.json")
    parser.add_argument("--github-repo", action="append", dest="github_repos")
    args = parser.parse_args()

    github_repos = args.github_repos or DEFAULT_GITHUB_REPOS
    digest = build_digest(research_sources(DEFAULT_SOURCES), github_repo_research(github_repos))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(digest, indent=2), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
