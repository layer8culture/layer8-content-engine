"""Reject incomplete exports and changes outside the five approved guide pages."""
from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path

from build_visual_guides import BASELINE, GUIDES, Document


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, default=Path("site"))
    args = parser.parse_args()
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", BASELINE, "site"], text=True
    ).splitlines()
    allowed = {f"guides/{slug}/index.html" for slug in GUIDES}
    allowed.update({"guides/field-guide.js", "guides/field-guide.css"})
    for source_path in paths:
        relative = source_path.removeprefix("site/")
        target = args.site / Path(relative)
        if not target.is_file():
            raise RuntimeError(f"Missing baseline file: {relative}")
        if relative in allowed:
            continue
        original = subprocess.check_output(["git", "show", f"{BASELINE}:{source_path}"])
        # Git checkout may expand LF to CRLF; the deployable artifact must not.
        if target.read_bytes() != original:
            raise RuntimeError(f"Non-guide production file changed: {relative}")
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
    for relative in allowed:
        if not (args.site / Path(relative)).is_file():
            raise RuntimeError(f"Missing guide release file: {relative}")
    print(f"Complete artifact: {len(paths)} baseline files, five visual guides, unchanged non-guide bytes.")
    for relative in sorted(allowed):
        print(hashlib.sha256((args.site / Path(relative)).read_bytes()).hexdigest(), relative)


if __name__ == "__main__":
    main()
