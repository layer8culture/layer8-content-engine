"""Snapshot the proven production export and verify every publicly served file."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import shutil
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--successful-deployment-run", type=int, required=True)
    parser.add_argument("--url", default="https://www.layer8culture.io")
    parser.add_argument("--original-checkout", type=Path)
    args = parser.parse_args()
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    archive = destination / "production-source.zip"
    subprocess.run(
        ["git", "archive", "--format=zip", f"--output={archive}", args.revision],
        check=True,
    )
    subprocess.run(
        ["git", "archive", "--format=zip", f"--output={destination / 'production-site.zip'}",
         args.revision, "site"], check=True,
    )
    shutil.unpack_archive(destination / "production-site.zip", destination / "restore")
    root = destination / "restore" / "site"

    def verify(path: Path) -> dict:
        relative = path.relative_to(root).as_posix()
        expected = path.read_bytes()
        result = {"path": relative, "sha256": digest(expected), "bytes": len(expected)}
        if relative == "staticwebapp.config.json":
            result["verification"] = "config from successful deployed source revision"
            return result
        route = relative[:-10] if relative.endswith("index.html") else relative
        request = urllib.request.Request(f"{args.url.rstrip('/')}/{route}",
                                         headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(request, timeout=60) as response:
            actual = response.read()
        if actual != expected:
            raise RuntimeError(f"Production differs from baseline: {relative}")
        result["verification"] = "live byte-for-byte match"
        return result

    files = sorted(path for path in root.rglob("*") if path.is_file())
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        manifest = list(pool.map(verify, files))
    if args.original_checkout:
        preserved = destination / "uncommitted-reference"
        preserved.mkdir()
        for relative in [
            "site", "campaigns/layer8culture-weekly-guides.json",
            "scripts/build_weekly_guide.py", ".github/workflows/deploy-layer8-site.yml",
        ]:
            source = args.original_checkout / Path(relative)
            target = preserved / Path(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
    record = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "production_url": args.url,
        "source_revision": args.revision,
        "successful_deployment_run_url": (
            f"https://github.com/layer8culture/layer8-content-engine/actions/runs/"
            f"{args.successful_deployment_run}"
        ),
        "azure_resource": "rg-layer8culture/swa-layer8culture",
        "successful_deployment_run": args.successful_deployment_run,
        "archives": {
            path.name: digest(path.read_bytes()) for path in destination.glob("*.zip")
        },
        "files": manifest,
    }
    (destination / "manifest.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    shutil.copy2(Path(__file__).with_name("rollback_site.ps1"), destination / "rollback.ps1")
    print(f"Verified {len(manifest) - 1} public files and deployed configuration.")
    print(f"Backup: {destination}")
    for name, value in record["archives"].items():
        print(f"{name} SHA256 {value}")


if __name__ == "__main__":
    main()
