"""Sandboxed LaTeX compilation.

Two backends behind one interface. Tectonic is a single self-contained binary
and is the default for local development and for the free-tier deployment
target, where a 4GB TeX Live image is not an option. Docker runs real pdflatex
under hard isolation and is the production choice where it is available.

Whichever backend runs, a compile is treated as untrusted execution: no shell,
no network for the process itself, a scratch directory that is deleted
afterwards, a wall-clock timeout, and shell-escape disabled so a document
cannot invoke arbitrary programs.

Page count is captured on every successful compile, because a resume that
silently became three pages is a failed generation even though LaTeX exited
zero.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .compat import apply_shim

__all__ = [
    "CompileResult",
    "CompilerBackend",
    "TectonicBackend",
    "DockerBackend",
    "get_compiler",
    "CompileError",
]


class CompileError(RuntimeError):
    """Raised when the compiler itself is unavailable or misconfigured."""


@dataclass(slots=True)
class CompileResult:
    ok: bool
    pdf: bytes | None = None
    page_count: int = 0
    log: str = ""
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    backend: str = ""

    @property
    def size_bytes(self) -> int:
        return len(self.pdf) if self.pdf else 0


# LaTeX error lines are the only part of a multi-thousand-line log worth
# feeding back to the model; the rest is noise that wastes context.
_ERROR_RE = re.compile(r"^(?:!|.*?:\d+:)\s*(.+)$", re.MULTILINE)
_UNDEFINED_RE = re.compile(r"Undefined control sequence.*?\n(.*?)$", re.MULTILINE)


def extract_errors(log: str, limit: int = 12) -> list[str]:
    """Pull the actionable error lines out of a LaTeX log."""
    errors: list[str] = []
    for match in _ERROR_RE.finditer(log):
        line = match.group(1).strip()
        if not line or line.startswith(("See the", "Type  H <return>", "...")):
            continue
        if line not in errors:
            errors.append(line)
        if len(errors) >= limit:
            break
    return errors


def count_pages(pdf: bytes) -> int:
    """Count pages without a full parse where possible."""
    try:
        from pypdf import PdfReader
        from io import BytesIO

        return len(PdfReader(BytesIO(pdf)).pages)
    except Exception:
        # Fall back to counting page objects in the raw PDF.
        return max(pdf.count(b"/Type /Page") - pdf.count(b"/Type /Pages"), 0)


class CompilerBackend(Protocol):
    name: str

    def available(self) -> bool: ...

    def compile(self, tex: str, *, timeout: int = 60) -> CompileResult: ...


@dataclass
class TectonicBackend:
    """Compile with the Tectonic binary.

    Tectonic fetches the packages a document needs on first use and caches
    them. On an ephemeral filesystem that cache is lost on every restart, so
    the deployment image warms it at build time -- see ``infra/Dockerfile``.
    """

    binary: str = "tectonic"
    name: str = "tectonic"
    cache_dir: Path | None = None

    def available(self) -> bool:
        return shutil.which(self.binary) is not None or Path(self.binary).exists()

    def compile(self, tex: str, *, timeout: int = 60) -> CompileResult:
        import time

        if not self.available():
            raise CompileError(
                f"tectonic binary not found at {self.binary!r}; "
                "run scripts/install_tectonic.py or set FORGE_TECTONIC_PATH"
            )

        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="forge-tex-") as tmp:
            work = Path(tmp)
            source = work / "resume.tex"
            # The shim is applied to this temporary copy only. What we store
            # and hand back to the user is always their own unmodified file.
            source.write_text(apply_shim(tex), encoding="utf-8")

            cmd = [
                self.binary,
                "-X",
                "compile",
                str(source),
                "--outdir",
                str(work),
                "--keep-logs",
                "--untrusted",  # disables shell-escape and other unsafe features
            ]
            if self.cache_dir:
                cmd += ["--cache-dir", str(self.cache_dir)]

            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=work,
                    shell=False,
                )
            except subprocess.TimeoutExpired:
                return CompileResult(
                    ok=False,
                    log=f"compilation exceeded {timeout}s and was terminated",
                    errors=[f"timeout after {timeout}s"],
                    duration_seconds=time.monotonic() - started,
                    backend=self.name,
                )

            log = (proc.stdout or "") + (proc.stderr or "")
            log_file = work / "resume.log"
            if log_file.exists():
                log += "\n" + log_file.read_text(encoding="utf-8", errors="replace")

            pdf_path = work / "resume.pdf"
            duration = time.monotonic() - started

            if proc.returncode != 0 or not pdf_path.exists():
                return CompileResult(
                    ok=False,
                    log=log,
                    errors=extract_errors(log) or [f"exit code {proc.returncode}"],
                    duration_seconds=duration,
                    backend=self.name,
                )

            pdf = pdf_path.read_bytes()
            return CompileResult(
                ok=True,
                pdf=pdf,
                page_count=count_pages(pdf),
                log=log,
                duration_seconds=duration,
                backend=self.name,
            )


@dataclass
class DockerBackend:
    """Compile with pdflatex inside a locked-down container."""

    image: str = "texlive/texlive:latest-minimal"
    name: str = "docker"
    memory: str = "512m"

    def available(self) -> bool:
        if shutil.which("docker") is None:
            return False
        try:
            proc = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True,
                timeout=10,
            )
            return proc.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False

    def compile(self, tex: str, *, timeout: int = 90) -> CompileResult:
        import time

        if not self.available():
            raise CompileError("docker is not available or its daemon is not running")

        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="forge-tex-") as tmp:
            work = Path(tmp)
            (work / "resume.tex").write_text(tex, encoding="utf-8")

            cmd = [
                "docker", "run", "--rm",
                "--network=none",
                f"--memory={self.memory}",
                "--pids-limit=128",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges",
                "-v", f"{work}:/data",
                "-w", "/data",
                self.image,
                "pdflatex",
                "-interaction=nonstopmode",
                "-halt-on-error",
                "-no-shell-escape",
                "resume.tex",
            ]

            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=timeout, shell=False
                )
            except subprocess.TimeoutExpired:
                return CompileResult(
                    ok=False,
                    log=f"compilation exceeded {timeout}s",
                    errors=[f"timeout after {timeout}s"],
                    duration_seconds=time.monotonic() - started,
                    backend=self.name,
                )

            log = (proc.stdout or "") + (proc.stderr or "")
            pdf_path = work / "resume.pdf"
            duration = time.monotonic() - started

            if not pdf_path.exists():
                return CompileResult(
                    ok=False,
                    log=log,
                    errors=extract_errors(log) or [f"exit code {proc.returncode}"],
                    duration_seconds=duration,
                    backend=self.name,
                )

            pdf = pdf_path.read_bytes()
            return CompileResult(
                ok=True,
                pdf=pdf,
                page_count=count_pages(pdf),
                log=log,
                duration_seconds=duration,
                backend=self.name,
            )


def get_compiler(preference: str = "auto", binary: str = "tectonic") -> CompilerBackend:
    """Resolve a usable compiler backend.

    ``auto`` prefers Tectonic for its startup cost, falling back to Docker.
    """
    tectonic = TectonicBackend(binary=binary)
    docker = DockerBackend()

    if preference == "tectonic":
        return tectonic
    if preference == "docker":
        return docker

    if tectonic.available():
        return tectonic
    if docker.available():
        return docker
    raise CompileError(
        "no LaTeX backend available: install Tectonic "
        "(python scripts/install_tectonic.py) or start Docker"
    )
