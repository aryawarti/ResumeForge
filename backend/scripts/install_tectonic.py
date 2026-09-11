"""Download the Tectonic binary into the project's tool directory.

Kept project-local rather than installed system-wide, so a checkout is
self-contained and nothing outside the repository is modified.

Set TECTONIC_VERSION to pin a release; the deployment image always does. The
unpinned path resolves "latest" through GitHub's API, which allows 60
unauthenticated requests an hour per IP. Cloud builders share IPs, so an
unpinned build fails at random -- and a pin keeps every image on the same
Tectonic besides.
"""

from __future__ import annotations

import io
import json
import os
import platform
import stat
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

RELEASES = "https://api.github.com/repos/tectonic-typesetting/tectonic/releases/latest"
DOWNLOADS = "https://github.com/tectonic-typesetting/tectonic/releases/download"
TOOLS_DIR = Path(__file__).resolve().parents[1] / ".tools"


def target_suffix() -> str:
    """The release-asset suffix for this machine."""
    system = platform.system().lower()
    arm = platform.machine().lower() in {"arm64", "aarch64"}

    if system == "windows":
        return "x86_64-pc-windows-msvc.zip"
    if system == "darwin":
        return "aarch64-apple-darwin.tar.gz" if arm else "x86_64-apple-darwin.tar.gz"
    # musl builds are statically linked, so they run on any Linux base image.
    return (
        "aarch64-unknown-linux-musl.tar.gz" if arm else "x86_64-unknown-linux-musl.tar.gz"
    )


def resolve_download() -> tuple[str, str]:
    """Return the download URL and the release tag it belongs to."""
    suffix = target_suffix()
    version = os.environ.get("TECTONIC_VERSION", "").strip()
    if version:
        name = f"tectonic-{version}-{suffix}"
        return f"{DOWNLOADS}/tectonic%40{version}/{name}", f"tectonic@{version}"

    print("resolving latest Tectonic release (set TECTONIC_VERSION to pin) ...")
    request = urllib.request.Request(
        RELEASES, headers={"Accept": "application/vnd.github+json"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        release = json.load(response)
    for asset in release["assets"]:
        if asset["name"].endswith(suffix):
            return asset["browser_download_url"], release["tag_name"]
    raise SystemExit(f"no Tectonic build published for {suffix}")


def main() -> int:
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    binary = TOOLS_DIR / ("tectonic.exe" if platform.system() == "Windows" else "tectonic")

    if binary.exists():
        print(f"already installed: {binary}")
        return 0

    url, tag = resolve_download()
    print(f"downloading {url} ...")
    with urllib.request.urlopen(url, timeout=600) as response:
        payload = response.read()
    print(f"downloaded {len(payload) / 1024 / 1024:.0f} MB")

    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            member = next(
                n for n in archive.namelist() if n.endswith(("tectonic.exe", "tectonic"))
            )
            binary.write_bytes(archive.read(member))
    else:
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            member = next(m for m in archive.getmembers() if m.name.endswith("tectonic"))
            extracted = archive.extractfile(member)
            assert extracted is not None
            binary.write_bytes(extracted.read())

    binary.chmod(binary.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"installed: {binary} ({tag})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
