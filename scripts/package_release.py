#!/usr/bin/env python3
"""Package a frozen player without regenerating its random draws.

Run from any directory: python scripts/package_release.py
Only Python's standard library is required. Repeated builds have identical bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build(output: Path) -> list[Path]:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("VERSION must contain a numeric major.minor.patch version")
    tag = f"v{version}"
    name = f"queueing-theory-player-{tag}"
    player = (ROOT / "releases" / tag / "index.html").read_bytes()
    if player != (ROOT / "figures" / "02_live_queue.html").read_bytes():
        raise ValueError("The frozen release and current offline player must match")
    html = player.decode("utf-8")
    payload = json.loads(html.split('data-role="model-data">', 1)[1].split("</script>", 1)[0])
    if payload.get("version") != version or f"Version {version}</a>" not in html:
        raise ValueError("Player version does not match VERSION")

    readme = f"""# Queueing Theory Player {tag}

Extract this folder, then open index.html in a modern browser.
No installation, server, or internet connection is required.

Enter the mean and standard deviation for both departments, select
Apply & restart, then Play or step through each day. Changes reuse the
same random draws so that comparisons remain meaningful.

Defaults: 500 working days; both Gaussian means 10 and standard deviations 2;
seed 42; development cost EUR 1,000 per feature. Capacities are rounded to
whole features and clamped at zero. Arrivals precede testing; the queue is FIFO.

This is one illustrative path in a simplified model, with independent days and
departments, equal feature sizes, and no rework, feedback, or sales process.

Fixed web player: https://sebastian-kom.github.io/queueing-theory/releases/{tag}/
Release: https://github.com/Sebastian-Kom/queueing-theory/releases/tag/{tag}
Source and model details: https://github.com/Sebastian-Kom/queueing-theory/tree/{tag}

The manifest records SHA-256 hashes for the files in this package.
""".encode("utf-8")
    files = {"index.html": player, "README.md": readme, "VERSION": (version + "\n").encode()}
    manifest = {
        "name": "Queueing Theory Player", "version": version, "source_ref": tag,
        "source_url": f"https://github.com/Sebastian-Kom/queueing-theory/tree/{tag}",
        "entrypoint": "index.html", "hash_algorithm": "sha256",
        "files": {path: digest(data) for path, data in files.items()},
    }
    files["manifest.json"] = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"{name}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path, data in sorted(files.items()):
            info = zipfile.ZipInfo(f"{name}/{path}", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, data)
    standalone = output / f"{name}.html"
    standalone.write_bytes(player)
    checksums = output / "SHA256SUMS.txt"
    checksums.write_text("".join(f"{digest(path.read_bytes())}  {path.name}\n"
                                  for path in (archive, standalone)), encoding="utf-8")
    return [archive, standalone, checksums]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    for artifact in build(args.output_dir):
        print(artifact.resolve())
