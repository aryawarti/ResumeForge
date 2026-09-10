"""Command-line driver for the agent pipeline.

Exists so the core can be exercised without the web stack. Two subcommands
matter during development:

    forge inspect  resume.tex              -- parse and print the structure
    forge tailor   resume.tex job.txt      -- run the full pipeline

``inspect`` needs no API key and is the fastest way to check whether a new
template parses correctly before spending a request on it.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from .agent.graph import run_pipeline
from .agent.report import build_change_report, build_gap_report
from .config import get_settings
from .latex.compiler import get_compiler
from .latex.parser import parse_resume

# Resumes and model rationales are full of typographic punctuation -- curly
# apostrophes, en dashes, non-breaking hyphens. The Windows console defaults
# to cp1252, which cannot encode any of them, and the result is a crash in
# the reporting step after the pipeline has already done its work. Replacing
# what cannot be represented is the right trade here: a report with a plain
# hyphen in it is still a report.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def cmd_inspect(args: argparse.Namespace) -> int:
    source = pathlib.Path(args.resume).read_text(encoding="utf-8")
    tree = parse_resume(source, args.profile)

    print(f"profile : {tree.profile}")
    print(f"summary : {tree.summary()}\n")
    for section in tree.sections:
        flag = " [skills]" if section.is_skills else ""
        print(f"  {section.id}  {section.title}{flag}")
        for entry in section.entries:
            print(f"      {entry.id}  {entry.label}")
            for bullet in entry.bullets:
                print(f"          {bullet.id}  {bullet.plain[:78]}")
        for bullet in section.loose_bullets:
            print(f"      (loose) {bullet.id}  {bullet.plain[:70]}")

    if tree.warnings:
        print("\nwarnings:")
        for warning in tree.warnings:
            print(f"  - {warning}")

    if args.verify_roundtrip:
        from .agent.apply import apply_plan
        from .agent.ops import EditPlan

        rendered = apply_plan(tree, EditPlan(strategy="verify", edits=[])).source
        ok = rendered == source
        print(f"\nround-trip: {'byte-identical' if ok else 'MISMATCH'}")
        if not ok:
            return 1
    return 0


def cmd_compile(args: argparse.Namespace) -> int:
    settings = get_settings()
    source = pathlib.Path(args.resume).read_text(encoding="utf-8")
    compiler = get_compiler(settings.compile_backend, binary=settings.tectonic_binary)
    result = compiler.compile(source, timeout=settings.compile_timeout_seconds)

    print(f"backend : {result.backend}")
    print(f"ok      : {result.ok}")
    print(f"pages   : {result.page_count}")
    print(f"time    : {result.duration_seconds:.1f}s")
    if not result.ok:
        for error in result.errors[:10]:
            print(f"  ERR: {error}")
        return 1
    if args.out and result.pdf:
        pathlib.Path(args.out).write_bytes(result.pdf)
        print(f"wrote   : {args.out}")
    return 0


def cmd_tailor(args: argparse.Namespace) -> int:
    resume_path = pathlib.Path(args.resume)
    source = resume_path.read_text(encoding="utf-8")
    job_text = pathlib.Path(args.job).read_text(encoding="utf-8")

    print(f"tailoring {resume_path.name} ...\n")
    result = run_pipeline(source, job_text, profile_name=args.profile)

    for event in result.events:
        stage = event.pop("stage")
        detail = " ".join(f"{k}={v}" for k, v in event.items())
        print(f"  {stage:24} {detail}")

    change = build_change_report(result.apply_result, result.plan.strategy)
    gaps = build_gap_report(result.job_spec, result.coverage)

    print(f"\nstrategy: {result.plan.strategy}\n")

    if change.reorderings:
        print("reorderings:")
        for item in change.reorderings:
            print(f"  - {item['op']} on {item['target_id']}: {item['reason']}")

    applied = [d for d in change.diffs if d.applied]
    rejected = [d for d in change.diffs if not d.applied]

    if applied:
        print("\nrewrites:")
        for diff in applied:
            print(f"  - {diff.reason}")
            print(f"      before: {diff.before}")
            print(f"      after : {diff.after}")

    if rejected:
        print("\nrejected by guards:")
        for diff in rejected:
            print(f"  - proposed: {diff.after}")
            print(f"      refused: {diff.rejection}")

    print(f"\ngaps ({gaps['coverage_score']:.0%} coverage):")
    for item in gaps["missing_required"]:
        print(f"  [required]  {item}")
    for item in gaps["missing_preferred"]:
        print(f"  [preferred] {item}")

    out_tex = pathlib.Path(args.out or f"{resume_path.stem}-tailored.tex")
    out_tex.write_text(result.source, encoding="utf-8")
    print(f"\nwrote {out_tex}")

    if result.pdf:
        out_pdf = out_tex.with_suffix(".pdf")
        out_pdf.write_bytes(result.pdf)
        print(f"wrote {out_pdf} ({result.page_count} page(s))")

    if args.json:
        payload = {
            "change_report": change.to_dict(),
            "gap_report": gaps,
            "coverage": result.coverage.model_dump(),
            "job_spec": result.job_spec.model_dump(),
        }
        pathlib.Path(args.json).write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )
        print(f"wrote {args.json}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forge", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser("inspect", help="parse a resume and show its structure")
    p_inspect.add_argument("resume")
    p_inspect.add_argument("--profile", default=None)
    p_inspect.add_argument("--verify-roundtrip", action="store_true")
    p_inspect.set_defaults(func=cmd_inspect)

    p_compile = sub.add_parser("compile", help="compile a resume to PDF")
    p_compile.add_argument("resume")
    p_compile.add_argument("--out", default=None)
    p_compile.set_defaults(func=cmd_compile)

    p_tailor = sub.add_parser("tailor", help="run the full tailoring pipeline")
    p_tailor.add_argument("resume")
    p_tailor.add_argument("job")
    p_tailor.add_argument("--profile", default=None)
    p_tailor.add_argument("--out", default=None)
    p_tailor.add_argument("--json", default=None)
    p_tailor.set_defaults(func=cmd_tailor)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
