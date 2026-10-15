"""Reject incomplete exports and changes outside the guides and Projects release."""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from pathlib import Path

from build_visual_guides import BASELINE, GUIDES, Document
from build_projects_showcase import PROJECTS_SCRIPT, PROJECTS_STYLESHEET


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, default=Path("site"))
    args = parser.parse_args()
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", BASELINE, "site"], text=True
    ).splitlines()
    guide_pages = {f"guides/{slug}/index.html" for slug in GUIDES}
    allowed = set(guide_pages)
    allowed.update({"guides/field-guide.js", "guides/field-guide.css"})
    project_files = {
        "projects/index.html",
        "projects/section.html",
        "projects/projects.css",
        "projects/projects.js",
        "projects/assets/deal-lab-screenshot.jpg",
        "projects/assets/green-foods-screenshot.jpg",
        "projects/assets/radio-loop.gif",
        "projects/assets/radio-poster.jpg",
    }
    index_files = {"sitemap.xml", "llms.txt"}
    allowed.update(project_files | index_files)
    nav_pages = set()
    for source_path in paths:
        relative = source_path.removeprefix("site/")
        target = args.site / Path(relative)
        if not target.is_file():
            raise RuntimeError(f"Missing baseline file: {relative}")
        original = subprocess.check_output(["git", "show", f"{BASELINE}:{source_path}"])
        if b"<nav" in original and b"aria-expanded=" in original:
            nav_pages.add(relative)
        if relative in guide_pages:
            continue
        current = target.read_bytes()
        if relative in nav_pages:
            if current.count(PROJECTS_SCRIPT) != 1:
                raise RuntimeError(f"Shared navigation missing Projects enhancement: {relative}")
            current = current.replace(PROJECTS_SCRIPT, b"", 1)
        elif PROJECTS_SCRIPT in current:
            raise RuntimeError(f"Unexpected Projects script outside shared navigation: {relative}")
        if relative != "projects/index.html" and PROJECTS_STYLESHEET in current:
            raise RuntimeError(f"Projects stylesheet added outside the Projects page: {relative}")
        if relative == "sitemap.xml":
            entry = (
                b"  <url>\n"
                b"    <loc>https://www.layer8culture.io/projects/</loc>\n"
                b"    <lastmod>2026-10-14</lastmod>\n"
                b"    <changefreq>monthly</changefreq>\n"
                b"    <priority>0.8</priority>\n"
                b"  </url>\n"
            )
            if current.count(entry) != 1:
                raise RuntimeError("Sitemap must include the Projects page exactly once.")
            current = current.replace(entry, b"", 1)
        elif relative == "llms.txt":
            entry = b"- https://www.layer8culture.io/projects/\n"
            if current.count(entry) != 1:
                raise RuntimeError("llms.txt must include the Projects page exactly once.")
            current = current.replace(entry, b"", 1)
        # Git checkout may expand LF to CRLF; the deployable artifact must not.
        if current != original:
            raise RuntimeError(f"Unrelated production file changed: {relative}")
    for slug in GUIDES:
        target = args.site / "guides" / slug / "index.html"
        source = target.read_text(encoding="utf-8")
        if "data-visual-guide" not in source:
            raise RuntimeError(f"Guide missing visual format: {slug}")
        doc = Document(source)
        for node in doc.find("script") + doc.find("link") + doc.find("img"):
            ref = node.attrs.get("src") or node.attrs.get("href", "")
            if ref.startswith("/") and not ref.startswith("//"):
                asset = args.site / Path(ref.split("?")[0].lstrip("/"))
                if not asset.is_file():
                    raise RuntimeError(f"Missing guide dependency: {ref}")
    for relative in project_files:
        if not (args.site / Path(relative)).is_file():
            raise RuntimeError(f"Missing Projects release file: {relative}")
    expected_files = {path.removeprefix("site/") for path in paths} | allowed
    actual_files = {
        path.relative_to(args.site).as_posix()
        for path in args.site.rglob("*") if path.is_file()
    }
    unexpected = actual_files - expected_files
    if unexpected:
        raise RuntimeError(f"Unexpected files in complete artifact: {', '.join(sorted(unexpected))}")
    for relative in nav_pages:
        source = (args.site / Path(relative)).read_bytes()
        if source.count(PROJECTS_SCRIPT) != 1:
            raise RuntimeError(f"Projects navigation missing from shared shell: {relative}")
    project_page = (args.site / "projects" / "index.html").read_text(encoding="utf-8")
    project_doc = Document(project_page)
    if "https://www.layer8culture.io/projects/" not in project_page:
        raise RuntimeError("Projects page must declare its canonical URL.")
    if "<title>Projects | Layer8.Culture</title>" not in project_page:
        raise RuntimeError("Projects page must declare its page title.")
    if len(project_doc.find("main")) != 1 or len(project_doc.find("footer")) != 1:
        raise RuntimeError("Projects page must reuse the shared page shell.")
    if len(project_doc.find("h1")) != 1:
        raise RuntimeError("Projects page must contain one page-level heading.")
    if project_page.count(PROJECTS_STYLESHEET.decode("ascii")) != 1:
        raise RuntimeError("Projects page must load its own stylesheet exactly once.")
    if PROJECTS_SCRIPT.decode("ascii") not in project_page:
        raise RuntimeError("Projects page must load the shared navigation enhancement.")
    if re.search(r'<script[^>]+/_next/static/chunks/', project_page) or "self.__next_f.push" in project_page:
        raise RuntimeError("Static Projects page must not start a mismatched Next hydration.")
    project_html = (args.site / "projects" / "section.html").read_text(encoding="utf-8")
    project_doc = Document(project_html)
    if len(project_doc.find("article")) != 4:
        raise RuntimeError("Projects section must contain exactly four project cards.")
    if "PREVIEW NOTES" in project_html or "<header" in project_html or "<footer" in project_html:
        raise RuntimeError("Standalone preview chrome must not ship inside the Projects section.")
    expected_links = {
        "https://therealestatedeallab.com/",
        "https://greenfoodsmarket.com/",
        "https://www.youtube.com/@layer8cultureradio",
        "https://sonar.layer8culture.io/",
    }
    actual_links = {
        node.attrs.get("href") for node in project_doc.find("a")
        if node.attrs.get("href", "").startswith("https://")
    }
    if actual_links != expected_links:
        raise RuntimeError("Projects section links do not match the approved four projects.")
    for node in project_doc.find("a"):
        if node.attrs.get("href", "").startswith("https://"):
            if node.attrs.get("target") != "_blank" or "noopener" not in node.attrs.get("rel", ""):
                raise RuntimeError("External project links must safely open in a new tab.")
    for node in project_doc.find("script") + project_doc.find("link") + project_doc.find("img"):
        ref = node.attrs.get("src") or node.attrs.get("href", "")
        if ref.startswith("/projects/"):
            asset = args.site / Path(ref.split("?")[0].lstrip("/"))
            if not asset.is_file():
                raise RuntimeError(f"Missing Projects dependency: {ref}")
    if "prefers-reduced-motion" not in (args.site / "projects" / "projects.css").read_text(encoding="utf-8"):
        raise RuntimeError("Projects animation styles must respect reduced motion.")
    for relative in allowed:
        if not (args.site / Path(relative)).is_file():
            raise RuntimeError(f"Missing release file: {relative}")
    print(f"Complete artifact: {len(paths)} baseline files, five visual guides, four Projects cards, standalone page, unchanged unrelated bytes.")
    for relative in sorted(allowed):
        print(hashlib.sha256((args.site / Path(relative)).read_bytes()).hexdigest(), relative)


if __name__ == "__main__":
    main()
