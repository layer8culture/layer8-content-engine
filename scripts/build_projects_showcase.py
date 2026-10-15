"""Attach the approved Projects section and navigation enhancement to the export."""
from __future__ import annotations

import argparse
from pathlib import Path

PROJECTS_SCRIPT = b'<script src="/projects/projects.js" defer></script>'
PROJECTS_STYLESHEET = b'<link rel="stylesheet" href="/projects/projects.css"/>'


def integrate_document(relative: Path, source: bytes) -> bytes:
    if b"<nav" not in source or b"aria-expanded=" not in source:
        return source
    if source.count(b"</head>") != 1:
        raise ValueError(f"Expected one document head in {relative}")
    additions = []
    if relative == Path("index.html") and PROJECTS_STYLESHEET not in source:
        additions.append(PROJECTS_STYLESHEET)
    if PROJECTS_SCRIPT not in source:
        additions.append(PROJECTS_SCRIPT)
    if not additions:
        return source
    return source.replace(b"</head>", b"".join(additions) + b"</head>", 1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, default=Path("site"))
    args = parser.parse_args()
    root = args.site
    if not (root / "projects" / "section.html").is_file():
        raise FileNotFoundError(root / "projects" / "section.html")
    if not (root / "projects" / "projects.css").is_file():
        raise FileNotFoundError(root / "projects" / "projects.css")
    if not (root / "projects" / "projects.js").is_file():
        raise FileNotFoundError(root / "projects" / "projects.js")
    changed = 0
    for page in sorted(root.rglob("*.html")):
        relative = page.relative_to(root)
        source = page.read_bytes()
        integrated = integrate_document(relative, source)
        if integrated != source:
            page.write_bytes(integrated)
            changed += 1
    print(f"Projects integration attached to {changed} shared-navigation pages.")


if __name__ == "__main__":
    main()
