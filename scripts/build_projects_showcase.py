"""Build the standalone Projects page and enhance shared navigation."""
from __future__ import annotations

import argparse
import html
import re
from pathlib import Path

PROJECTS_SCRIPT = b'<script src="/projects/projects.js" defer></script>'
PROJECTS_STYLESHEET = b'<link rel="stylesheet" href="/projects/projects.css"/>'
PROJECTS_TITLE = "Projects | Layer8.Culture"
PROJECTS_DESCRIPTION = (
    "A few projects where technology meets everyday life. Real people. Real builds."
)


def build_projects_page(homepage: bytes, section: bytes) -> bytes:
    source = homepage.decode("utf-8")
    head_match = re.search(r"<head>(.*?)</head>", source, re.DOTALL)
    nav_match = re.search(r"<nav\b.*?</nav>", source, re.DOTALL)
    footer_match = re.search(r"<footer\b.*?</footer>", source, re.DOTALL)
    html_match = re.search(r"<html\b[^>]*>", source)
    body_match = re.search(r"<body\b[^>]*>", source)
    if not all((head_match, nav_match, footer_match, html_match, body_match)):
        raise ValueError("Expected the exported homepage head, navigation, and footer.")

    head = head_match.group(1)
    head = head.replace(PROJECTS_STYLESHEET.decode("ascii"), "")
    head = head.replace(PROJECTS_SCRIPT.decode("ascii"), "")
    head = re.sub(r'<script\b[^>]*src="/_next/static/[^"]*"[^>]*></script>', "", head)
    head = re.sub(r'<script\b[^>]*src="/_next/static/[^"]*"[^>]*/>', "", head)
    head = re.sub(r'<script\b[^>]*src="/03~[^"]*"[^>]*></script>', "", head)
    head = re.sub(r'<script\b[^>]*src="/03~[^"]*"[^>]*/>', "", head)
    head = re.sub(r'<link\b(?=[^>]*rel="preload")(?=[^>]*as="script")[^>]*/>', "", head)
    head = re.sub(r'<link\b(?=[^>]*rel="preload")(?=[^>]*as="image")[^>]*/>', "", head)
    head = re.sub(r'<meta name="next-size-adjust" content=""/>', "", head)
    head = re.sub(r"<title>.*?</title>", f"<title>{html.escape(PROJECTS_TITLE)}</title>",
                  head, count=1, flags=re.DOTALL)
    description = html.escape(PROJECTS_DESCRIPTION, quote=True)
    head = re.sub(
        r'(<meta name="description" content=")[^"]*(" */?>)',
        rf"\g<1>{description}\2", head, count=1,
    )
    head = re.sub(r'(<link rel="canonical" href=")[^"]*(" */?>)',
                  r"\g<1>https://www.layer8culture.io/projects/\2", head, count=1)
    head = re.sub(r'(<meta property="og:title" content=")[^"]*(" */?>)',
                  rf"\g<1>{html.escape(PROJECTS_TITLE, quote=True)}\2", head, count=1)
    head = re.sub(r'(<meta property="og:description" content=")[^"]*(" */?>)',
                  rf"\g<1>{description}\2", head, count=1)
    head = re.sub(r'(<meta property="og:url" content=")[^"]*(" */?>)',
                  r"\g<1>https://www.layer8culture.io/projects/\2", head, count=1)
    head = re.sub(r'(<meta name="twitter:title" content=")[^"]*(" */?>)',
                  rf"\g<1>{html.escape(PROJECTS_TITLE, quote=True)}\2", head, count=1)
    head = re.sub(r'(<meta name="twitter:description" content=")[^"]*(" */?>)',
                  rf"\g<1>{html.escape(PROJECTS_DESCRIPTION, quote=True)}\2", head, count=1)
    head += PROJECTS_STYLESHEET.decode("ascii") + PROJECTS_SCRIPT.decode("ascii")
    projects_markup = section.decode("utf-8")
    projects_markup = projects_markup.replace(
        '<h2 id="projects-heading"', '<h1 id="projects-heading"', 1
    ).replace("</h2>", "</h1>", 1)

    document = (
        "<!DOCTYPE html>" + html_match.group(0) + "<head>" + head + "</head>"
        + body_match.group(0) + nav_match.group(0)
        + '<main class="l8-projects-page">' + projects_markup + "</main>"
        + footer_match.group(0) + "</body></html>"
    )
    return document.encode("utf-8")


def integrate_document(relative: Path, source: bytes) -> bytes:
    if b"<nav" not in source or b"aria-expanded=" not in source:
        return source
    if source.count(b"</head>") != 1:
        raise ValueError(f"Expected one document head in {relative}")
    if PROJECTS_SCRIPT in source:
        return source
    return source.replace(b"</head>", PROJECTS_SCRIPT + b"</head>", 1)


def update_site_indexes(root: Path) -> None:
    sitemap = root / "sitemap.xml"
    sitemap_source = sitemap.read_text(encoding="utf-8")
    projects_url = "https://www.layer8culture.io/projects/"
    if projects_url not in sitemap_source:
        projects_entry = (
            "  <url>\n"
            f"    <loc>{projects_url}</loc>\n"
            "    <lastmod>2026-10-14</lastmod>\n"
            "    <changefreq>monthly</changefreq>\n"
            "    <priority>0.8</priority>\n"
            "  </url>\n"
        )
        homepage_entry = "  </url>\n"
        sitemap_source = sitemap_source.replace(
            homepage_entry, homepage_entry + projects_entry, 1
        )
        sitemap.write_text(sitemap_source, encoding="utf-8", newline="\n")

    llms = root / "llms.txt"
    llms_source = llms.read_text(encoding="utf-8")
    projects_line = f"- {projects_url}"
    if projects_line not in llms_source:
        llms_source = llms_source.replace(
            "- https://www.layer8culture.io/\n",
            "- https://www.layer8culture.io/\n" + projects_line + "\n",
            1,
        )
        llms.write_text(llms_source, encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, default=Path("site"))
    args = parser.parse_args()
    root = args.site
    homepage = root / "index.html"
    section = root / "projects" / "section.html"
    if not homepage.is_file():
        raise FileNotFoundError(homepage)
    if not section.is_file():
        raise FileNotFoundError(section)
    if not (root / "projects" / "projects.css").is_file():
        raise FileNotFoundError(root / "projects" / "projects.css")
    if not (root / "projects" / "projects.js").is_file():
        raise FileNotFoundError(root / "projects" / "projects.js")
    homepage_bytes = homepage.read_bytes().replace(PROJECTS_STYLESHEET, b"")
    if homepage_bytes != homepage.read_bytes():
        homepage.write_bytes(homepage_bytes)
    project_page = root / "projects" / "index.html"
    project_page.write_bytes(build_projects_page(homepage_bytes, section.read_bytes()))
    changed = 0
    for page in sorted(root.rglob("*.html")):
        relative = page.relative_to(root)
        source = page.read_bytes()
        integrated = integrate_document(relative, source)
        if integrated != source:
            page.write_bytes(integrated)
            changed += 1
    update_site_indexes(root)
    print(f"Projects page generated; shared navigation enhancement attached to {changed} pages.")


if __name__ == "__main__":
    main()
