"""Command-line entry point for the tax-prep skill.

This is a thin wrapper over :func:`skill.scripts.pipeline.run_pipeline`
and :func:`skill.scripts.generate_schema.generate`. It lets a user (or a
shell script) drive the same end-to-end flow Claude drives from the
``SKILL.md`` interview, without going through Claude Code first.

Subcommands
-----------

* ``tax-prep run`` — ingest → compute → render → emit. Same contract as
  ``run_pipeline``: needs a directory of source PDFs and a
  ``taxpayer_info.json`` carrying the header fields PDFs cannot extract.
* ``tax-prep schema`` — print the current ``CanonicalReturn`` JSON
  schema on stdout (useful for piping into ``jq``, generating IDE
  completions, or diffing against ``skill/schemas/return.schema.json``).
* ``tax-prep version`` — print the installed package version on stdout.

The CLI uses only :mod:`argparse` from the stdlib — no new runtime deps.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


def _package_version() -> str:
    """Resolve the installed ``tax-prep-skill`` distribution version.

    Falls back to the ``[project].version`` string parsed from
    ``pyproject.toml`` when the package is not installed (e.g. when
    running the module directly from a source checkout without
    ``pip install -e .``). This keeps ``tax-prep version`` useful during
    local development.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("tax-prep-skill")
    except Exception:  # pragma: no cover - importlib always present on 3.11+
        pass

    # Fallback: read the version line out of pyproject.toml relative to
    # this file. Layout is <repo>/skill/scripts/cli.py → <repo>/pyproject.toml.
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if pyproject.exists():
        for line in pyproject.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("version") and "=" in stripped:
                # version = "0.1.0"
                _, _, rhs = stripped.partition("=")
                return rhs.strip().strip('"').strip("'")
    return "0.0.0+unknown"


def _cmd_run(args: argparse.Namespace) -> int:
    from pydantic import ValidationError

    from skill.scripts.pipeline import run_pipeline

    input_dir = Path(args.input).expanduser().resolve()
    taxpayer_info = Path(args.taxpayer_info).expanduser().resolve()
    output_dir = Path(args.output).expanduser().resolve()

    try:
        result = run_pipeline(
            input_dir=input_dir,
            taxpayer_info_path=taxpayer_info,
            output_dir=output_dir,
            build_paper_bundle=not args.no_bundle,
            emit_ffff_map=not args.no_ffff,
        )
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(
            f"error: {taxpayer_info} is not valid JSON: {exc.msg} "
            f"(line {exc.lineno}, column {exc.colno})",
            file=sys.stderr,
        )
        return 2
    except ValidationError as exc:
        print(
            f"error: {taxpayer_info} does not match the CanonicalReturn schema:",
            file=sys.stderr,
        )
        for err in exc.errors():
            loc = ".".join(str(x) for x in err["loc"])
            print(f"  {loc}: {err['msg']}", file=sys.stderr)
        return 2

    # Human-readable summary on stdout. Structured data stays in
    # ``result.json`` inside ``output_dir``.
    computed = result.canonical_return.computed
    print(f"tax year:            {result.canonical_return.tax_year}")
    print(f"filing status:       {result.canonical_return.filing_status}")
    print(f"AGI:                 {computed.adjusted_gross_income}")
    print(f"taxable income:      {computed.taxable_income}")
    print(f"total tax:           {computed.total_tax}")
    if computed.refund is not None:
        print(f"refund:              {computed.refund}")
    if computed.amount_owed is not None:
        print(f"amount owed:         {computed.amount_owed}")
    print(f"rendered artifacts:  {len(result.rendered_paths)}")
    for p in result.rendered_paths:
        print(f"  - {p}")
    if result.warnings:
        print(f"warnings ({len(result.warnings)}):")
        for w in result.warnings:
            print(f"  - {w}")
    return 0


def _cmd_schema(args: argparse.Namespace) -> int:
    from skill.scripts.generate_schema import generate

    schema = generate()
    print(json.dumps(schema, indent=2, sort_keys=True))
    return 0


def _cmd_setup_gmail(args: argparse.Namespace) -> int:
    from skill.scripts.gmail_setup import setup

    return setup()


def _cmd_scan_email(args: argparse.Namespace) -> int:
    from skill.scripts.email_scanner import scan_gmail

    credentials_path = (
        Path(args.credentials).expanduser().resolve()
        if args.credentials
        else None  # scan_gmail will use bundled credentials
    )
    output_dir = Path(args.output).expanduser().resolve()

    print(f"Scanning Gmail for TY{args.tax_year} tax documents...")
    result = scan_gmail(
        credentials_path=credentials_path,
        output_dir=output_dir,
        tax_year=args.tax_year,
        filter_tax_filenames=args.filter_tax_only,
    )

    print(f"messages found:        {result.messages_found}")
    print(f"PDFs downloaded:       {len(result.attachments_downloaded)}")
    if result.skipped_non_pdf:
        print(f"skipped (not PDF):     {result.skipped_non_pdf}")
    if result.skipped_non_tax:
        print(f"skipped (not tax doc): {result.skipped_non_tax}")
    if result.errors:
        print(f"errors:                {len(result.errors)}")
        for e in result.errors:
            print(f"  - {e}")

    if result.attachments_downloaded:
        print(f"\nDownloaded to {output_dir}:")
        for att in result.attachments_downloaded:
            size_kb = att.size_bytes / 1024
            print(f"  {att.filename} ({size_kb:.0f} KB) — from: {att.sender}")

        if args.run_pipeline:
            print(f"\nRunning pipeline on {len(result.attachments_downloaded)} PDFs...")
            taxpayer_info = Path(args.taxpayer_info).expanduser().resolve()
            pipeline_output = Path(args.pipeline_output).expanduser().resolve()
            return _cmd_run(
                argparse.Namespace(
                    input=str(output_dir),
                    taxpayer_info=str(taxpayer_info),
                    output=str(pipeline_output),
                    no_bundle=False,
                    no_ffff=False,
                )
            )
        else:
            print(
                f"\nTo process these, run:\n"
                f"  tax-prep run --input {output_dir} "
                f"--taxpayer-info taxpayer.json --output ./out"
            )
    else:
        print("\nNo tax document PDFs found in this scan.")

    return 0


def _cmd_version(args: argparse.Namespace) -> int:
    print(_package_version())
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argparse parser.

    Exposed as a function so tests can invoke it without going through
    ``sys.argv``. The subcommand dispatch goes through ``args.func``.
    """
    parser = argparse.ArgumentParser(
        prog="tax-prep",
        description=(
            "Prepare US individual income tax returns (federal + all "
            "states) for TY2025+. Thin CLI wrapper over the "
            "skill.scripts.pipeline pipeline."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.required = True

    # run
    run_p = subparsers.add_parser(
        "run",
        help="Run the full ingest -> compute -> render pipeline.",
        description=(
            "Ingest every PDF in --input, merge header fields from "
            "--taxpayer-info, compute federal and state returns, and "
            "render filled PDFs + result.json into --output."
        ),
    )
    run_p.add_argument(
        "--input",
        required=True,
        help="Directory containing source PDFs (W-2s, 1099s, etc).",
    )
    run_p.add_argument(
        "--taxpayer-info",
        required=True,
        help=(
            "Path to taxpayer_info.json — a partial CanonicalReturn dict "
            "with header fields the PDFs cannot supply."
        ),
    )
    run_p.add_argument(
        "--output",
        required=True,
        help="Directory to write rendered PDFs and result.json.",
    )
    run_p.add_argument(
        "--no-bundle",
        action="store_true",
        help="Skip paper_bundle.pdf assembly.",
    )
    run_p.add_argument(
        "--no-ffff",
        action="store_true",
        help="Skip FFFF entry map emission (ffff_entries.json/.txt).",
    )
    run_p.set_defaults(func=_cmd_run)

    # setup-gmail
    setup_p = subparsers.add_parser(
        "setup-gmail",
        help="Set up Gmail API credentials for email scanning.",
        description=(
            "Interactive setup for Gmail API OAuth2 credentials. "
            "Walk through creating a Google Cloud project, enabling "
            "the Gmail API, and downloading client credentials."
        ),
    )
    setup_p.set_defaults(func=_cmd_setup_gmail)

    # scan-email
    scan_p = subparsers.add_parser(
        "scan-email",
        help="Scan Gmail for tax document PDF attachments.",
        description=(
            "Search Gmail for W-2, 1099, 1098, 1095-A, SSA-1099, "
            "and K-1 PDF attachments. Downloads them to --output "
            "for processing by 'tax-prep run'."
        ),
    )
    scan_p.add_argument(
        "--credentials",
        default=None,
        help=(
            "Path to Google OAuth2 client_secret JSON file. "
            "Default: uses bundled credentials (no setup needed)."
        ),
    )
    scan_p.add_argument(
        "--output",
        required=True,
        help="Directory to save downloaded PDF attachments.",
    )
    scan_p.add_argument(
        "--tax-year",
        type=int,
        default=2025,
        help="Tax year to search for (default: 2025).",
    )
    scan_p.add_argument(
        "--filter-tax-only",
        action="store_true",
        help=(
            "Only download PDFs whose filenames match tax document "
            "patterns (W-2, 1099, etc). Without this flag, downloads "
            "all PDF attachments from matching emails."
        ),
    )
    scan_p.add_argument(
        "--run-pipeline",
        action="store_true",
        help="Automatically run the pipeline on downloaded PDFs.",
    )
    scan_p.add_argument(
        "--taxpayer-info",
        default="taxpayer.json",
        help=(
            "Path to taxpayer_info.json (only used with --run-pipeline). "
            "Default: taxpayer.json in current directory."
        ),
    )
    scan_p.add_argument(
        "--pipeline-output",
        default="./out",
        help=(
            "Pipeline output directory (only used with --run-pipeline). "
            "Default: ./out"
        ),
    )
    scan_p.set_defaults(func=_cmd_scan_email)

    # schema
    schema_p = subparsers.add_parser(
        "schema",
        help="Print the CanonicalReturn JSON schema to stdout.",
    )
    schema_p.set_defaults(func=_cmd_schema)

    # version
    version_p = subparsers.add_parser(
        "version",
        help="Print the tax-prep-skill package version to stdout.",
    )
    version_p.set_defaults(func=_cmd_version)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Module entry point.

    Returns the exit code. Installed as the ``tax-prep`` console script
    via ``[project.scripts]`` in ``pyproject.toml``.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
