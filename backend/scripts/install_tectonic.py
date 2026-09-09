"""Download the Tectonic binary into the project's tool directory.

Kept project-local rather than installed system-wide, so a checkout is
self-contained and nothing outside the repository is modified. The deployment
image runs the same script, then warms the package cache with a throwaway
compile so the first real request does not pay the download cost.
"""

from __future__ import annotations

import io
import json
import platform
import stat
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

RELEASES = "https://api.github.com/repos/tectonic-typesetting/tectonic/releases/latest"
TOOLS_DIR = Path(__file__).resolve().parents[1] / ".tools"


def pick_asset(assets: list[dict]) -> dict:
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        wanted = "x86_64-pc-windows-msvc.zip"
    elif system == "darwin":
        wanted = (
            "aarch64-apple-darwin.tar.gz"
            if machine in {"arm64", "aarch64"}
            else "x86_64-apple-darwin.tar.gz"
        )
    else:
        wanted = (
            "aarch64-unknown-linux-musl.tar.gz"
            if machine in {"arm64", "aarch64"}
            else "x86_64-unknown-linux-musl.tar.gz"
        )

    for asset in assets:
        if asset["name"].endswith(wanted):
            return asset
    raise SystemExit(f"no Tectonic build published for {system}/{machine}")


def main() -> int:
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    binary = TOOLS_DIR / ("tectonic.exe" if platform.system() == "Windows" else "tectonic")

    if binary.exists():
        print(f"already installed: {binary}")
        return 0

    print("resolving latest Tectonic release ...")
    request = urllib.request.Request(
        RELEASES, headers={"Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        release = json.load(response)

    asset = pick_asset(release["assets"])
    size_mb = asset["size"] / 1024 / 1024
    print(f"downloading {asset['name']} ({size_mb:.0f} MB) ...")

    with urllib.request.urlopen(asset["browser_download_url"], timeout=600) as response:
        payload = response.read()

    if asset["name"].endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            member = next(
                n for n in archive.namelist() if n.endswith(("tectonic.exe", "tectonic"))
            )
            binary.write_bytes(archive.read(member))
    else:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            member = next(
                m for m in archive.getmembers() if m.name.endswith("tectonic")
            )
            extracted = archive.extractfile(member)
            assert extracted is not None
            binary.write_bytes(extracted.read())

    binary.chmod(binary.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"installed: {binary}")
    print(f"version tag: {release['tag_name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
