"""Compare a deployed complete site against the local release, including headers."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".md", ".svg", ".txt", ".xml"}


def comparable_content(path: Path, content: bytes) -> bytes:
    if path.suffix.lower() in TEXT_SUFFIXES:
        return content.replace(b"\r\n", b"\n")
    return content


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--site", type=Path, default=Path("site"))
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    config = json.loads((args.site / "staticwebapp.config.json").read_text(encoding="utf-8"))

    def verify(path: Path) -> dict:
        relative = path.relative_to(args.site).as_posix()
        route = relative[:-10] if relative.endswith("index.html") else relative
        request = urllib.request.Request(
            f"{args.url.rstrip('/')}/{route}", headers={"Cache-Control": "no-cache"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read()
            headers = response.headers
            if response.status != 200:
                raise RuntimeError(f"Unexpected status for {relative}: {response.status}")
        expected = path.read_bytes()
        if comparable_content(path, body) != comparable_content(path, expected):
            raise RuntimeError(f"Live release mismatch: {relative}")
        for name, value in config.get("globalHeaders", {}).items():
            if headers.get(name) != value:
                raise RuntimeError(f"Configuration header mismatch: {relative}: {name}")
        return {"path": relative, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}

    files = sorted(path for path in args.site.rglob("*")
                   if path.is_file() and path.name != "staticwebapp.config.json")
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        verified = list(pool.map(verify, files))
    report = {
        "verified_utc": datetime.now(timezone.utc).isoformat(),
        "url": args.url, "public_files": verified,
        "configuration": "All global response headers match the preserved configuration",
    }
    if args.report:
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"Verified {len(verified)} live files (text compared with normalized line endings) "
        f"and all configured response headers: {args.url}"
    )


if __name__ == "__main__":
    main()
