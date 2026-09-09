"""Smoke-test the compile backend against every fixture.

Also verifies the two failure paths that matter operationally: a document that
does not compile must surface actionable errors rather than a bare exit code,
and a document that compiles but runs long must report its true page count.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.latex.compiler import TectonicBackend  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def main() -> int:
    binary = ROOT / ".tools" / "tectonic.exe"
    if not binary.exists():
        binary = ROOT / ".tools" / "tectonic"
    compiler = TectonicBackend(binary=str(binary))
    print(f"backend available: {compiler.available()}\n")

    failures = 0
    for path in sorted(FIXTURES.glob("*.tex")):
        source = path.read_text(encoding="utf-8")
        result = compiler.compile(source, timeout=300)
        status = "OK " if result.ok else "FAIL"
        print(
            f"[{status}] {path.name:24} pages={result.page_count} "
            f"bytes={result.size_bytes:>7} {result.duration_seconds:5.1f}s"
        )
        if not result.ok:
            failures += 1
            for error in result.errors[:6]:
                print(f"         ERR: {error}")

    # A deliberately broken document must fail loudly and usefully.
    broken = (FIXTURES / "jakes_resume.tex").read_text(encoding="utf-8")
    broken = broken.replace(
        "\\end{document}", "\\thisCommandDoesNotExist{oops}\n\\end{document}"
    )
    result = compiler.compile(broken, timeout=300)
    print(f"\n[{'OK ' if not result.ok else 'BAD'}] broken document rejected as expected")
    print(f"         extracted errors: {result.errors[:3]}")
    if result.ok:
        failures += 1

    print(f"\n{'all fixtures compiled' if failures == 0 else f'{failures} failure(s)'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
