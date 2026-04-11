"""Tests for the ``tax-prep`` CLI entry point.

The CLI is a thin wrapper over :func:`skill.scripts.pipeline.run_pipeline`
so these tests only verify:

1. ``--help`` parses and exits cleanly.
2. ``tax-prep schema`` prints valid JSON matching the Pydantic schema.
3. ``tax-prep version`` prints the version declared in pyproject.toml.
4. ``tax-prep run`` with a minimal taxpayer_info.json produces
   ``result.json`` and ``form_1040.pdf`` without blowing up.

The synthetic taxpayer_info.json is the same shape used by
``test_pipeline.py::_write_minimal_taxpayer_json`` — duplicated here so
this test module stays independent.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from skill.scripts.cli import _package_version, build_parser, main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_minimal_taxpayer_json(path: Path) -> None:
    """Synthetic header-only taxpayer_info.json.

    Mirrors the helper in ``test_pipeline.py``; copied rather than
    imported to keep test modules independent.
    """
    data = {
        "schema_version": "0.1.0",
        "tax_year": 2025,
        "filing_status": "single",
        "taxpayer": {
            "first_name": "Alex",
            "last_name": "Doe",
            "ssn": "111-22-3333",
            "date_of_birth": "1985-01-01",
        },
        "address": {
            "street1": "1 Test Lane",
            "city": "Springfield",
            "state": "IL",
            "zip": "62701",
            "country": "US",
        },
    }
    path.write_text(json.dumps(data, indent=2))


def _read_pyproject_version() -> str:
    """Parse ``version = "..."`` out of pyproject.toml."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    for line in pyproject.read_text().splitlines():
        stripped = line.strip()
        if stripped.startswith("version") and "=" in stripped:
            _, _, rhs = stripped.partition("=")
            return rhs.strip().strip('"').strip("'")
    raise RuntimeError("version not found in pyproject.toml")


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


class TestCLIHelp:
    def test_top_level_help_exits_zero(self, capsys: pytest.CaptureFixture):
        parser = build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "tax-prep" in captured.out
        assert "run" in captured.out
        assert "schema" in captured.out
        assert "version" in captured.out

    def test_run_subcommand_help(self, capsys: pytest.CaptureFixture):
        parser = build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["run", "--help"])
        assert exc.value.code == 0
        captured = capsys.readouterr()
        assert "--input" in captured.out
        assert "--taxpayer-info" in captured.out
        assert "--output" in captured.out
        assert "--no-bundle" in captured.out
        assert "--no-ffff" in captured.out


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


class TestCLISchema:
    def test_schema_prints_valid_json(self, capsys: pytest.CaptureFixture):
        rc = main(["schema"])
        assert rc == 0
        captured = capsys.readouterr()
        parsed = json.loads(captured.out)
        assert parsed.get("title") == "CanonicalReturn"
        assert "properties" in parsed
        # ``$schema`` and ``$id`` are set by generate_schema.generate()
        assert parsed.get("$schema", "").startswith("https://json-schema.org")


# ---------------------------------------------------------------------------
# version
# ---------------------------------------------------------------------------


class TestCLIVersion:
    def test_version_matches_pyproject(self, capsys: pytest.CaptureFixture):
        expected = _read_pyproject_version()
        rc = main(["version"])
        assert rc == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == expected

    def test_package_version_helper_returns_str(self):
        """``_package_version`` must always return a string, even when
        importlib.metadata does not know about the distribution."""
        v = _package_version()
        assert isinstance(v, str)
        assert len(v) > 0


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


class TestCLIRun:
    def test_run_header_only_produces_artifacts(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        """Empty PDF directory + minimal taxpayer_info.json is the
        simplest possible pipeline call. The CLI should forward both
        paths, run_pipeline should produce a zero-income return with
        standard deduction, and result.json + form_1040.pdf should
        land in the output dir."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        taxpayer_info = tmp_path / "taxpayer_info.json"
        _write_minimal_taxpayer_json(taxpayer_info)
        output_dir = tmp_path / "output"

        rc = main(
            [
                "run",
                "--input",
                str(input_dir),
                "--taxpayer-info",
                str(taxpayer_info),
                "--output",
                str(output_dir),
            ]
        )
        assert rc == 0

        # result.json written and parseable
        result_json = output_dir / "result.json"
        assert result_json.exists()
        parsed = json.loads(result_json.read_text())
        assert parsed["tax_year"] == 2025
        assert parsed["filing_status"] == "single"

        # form_1040.pdf rendered
        assert (output_dir / "form_1040.pdf").exists()

        # FFFF map emitted by default
        assert (output_dir / "ffff_entries.json").exists()
        assert (output_dir / "ffff_entries.txt").exists()

        # Human-readable summary printed
        captured = capsys.readouterr()
        assert "tax year:" in captured.out
        assert "AGI:" in captured.out
        assert "rendered artifacts:" in captured.out

    def test_run_no_bundle_no_ffff_flags(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ):
        """``--no-bundle`` and ``--no-ffff`` must suppress the paper
        bundle and FFFF emission respectively."""
        input_dir = tmp_path / "input"
        input_dir.mkdir()
        taxpayer_info = tmp_path / "taxpayer_info.json"
        _write_minimal_taxpayer_json(taxpayer_info)
        output_dir = tmp_path / "output"

        rc = main(
            [
                "run",
                "--input",
                str(input_dir),
                "--taxpayer-info",
                str(taxpayer_info),
                "--output",
                str(output_dir),
                "--no-bundle",
                "--no-ffff",
            ]
        )
        assert rc == 0

        # Loose PDFs still present
        assert (output_dir / "form_1040.pdf").exists()
        # Bundle and FFFF map suppressed
        assert not (output_dir / "paper_bundle.pdf").exists()
        assert not (output_dir / "ffff_entries.json").exists()
        assert not (output_dir / "ffff_entries.txt").exists()

    def test_run_missing_input_dir_raises(self, tmp_path: Path):
        taxpayer_info = tmp_path / "taxpayer_info.json"
        _write_minimal_taxpayer_json(taxpayer_info)
        with pytest.raises(FileNotFoundError):
            main(
                [
                    "run",
                    "--input",
                    str(tmp_path / "does_not_exist"),
                    "--taxpayer-info",
                    str(taxpayer_info),
                    "--output",
                    str(tmp_path / "output"),
                ]
            )


# ---------------------------------------------------------------------------
# Subprocess smoke test — confirms the console_scripts entry point is
# wired when the package is pip-installed. Skipped when the entry point
# is not on PATH (e.g. when tests run against a non-installed tree).
# ---------------------------------------------------------------------------


class TestCLIConsoleScript:
    def test_console_script_help_runs(self):
        """``tax-prep --help`` via the installed console script.

        Resolves the script by looking next to ``sys.executable`` first
        (works when pytest runs against the project venv without that
        venv being on ``PATH``), then falls back to ``shutil.which``.
        Skips only if neither resolution finds it.
        """
        import shutil

        candidate = Path(sys.executable).parent / "tax-prep"
        if candidate.exists():
            exe = str(candidate)
        else:
            exe = shutil.which("tax-prep")
        if exe is None:
            pytest.skip(
                "tax-prep console script not installed; run pip install -e ."
            )
        proc = subprocess.run(
            [exe, "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0
        assert "tax-prep" in proc.stdout
        assert "run" in proc.stdout

    def test_python_m_cli_help_runs(self):
        """``python -m skill.scripts.cli --help`` should also work,
        since ``main`` is exposed at module level."""
        proc = subprocess.run(
            [sys.executable, "-m", "skill.scripts.cli", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0
        assert "tax-prep" in proc.stdout
