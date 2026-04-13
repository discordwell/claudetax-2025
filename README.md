# Tax Prep Skill

A Claude Code skill that prepares US individual income tax returns (federal plus all states with an income tax and DC) for Tax Year 2025 and later. The skill ships as a Python package with a CLI entry point and a `SKILL.md` interview prompt that Claude Code loads when invoked.

Status: alpha. Wave 6 lands the full 50-state plugin set, the FFFF entry map, paper-bundle assembly, and this packaging layer. Numbers are validated against hand-computed golden fixtures in `skill/tests/` (3500+ tests at `skill/tests/`).

## What it does

Given a directory of source PDFs (W-2s, 1099s, K-1s, prior-year return) and a header JSON file carrying the fields that cannot be extracted from PDFs (taxpayer name, SSN, filing status, address, dependents), the skill:

1. Ingests every PDF via a tiered cascade — AcroForm extraction first, text layer second, OCR third — and merges the extracted fields into a `CanonicalReturn` dict (`skill/scripts/pipeline.py:404`).
2. Validates the assembled dict against `skill/scripts/models.py::CanonicalReturn` and runs `compute()` (`skill/scripts/calc/engine.py`) to produce every line on Form 1040 and the applicable schedules.
3. Dispatches per-state plugins for the resident state and every state that appears in a W-2 state row (`skill/scripts/states/` — 50 states + DC).
4. Renders filled IRS PDFs — Form 1040 plus Schedules A, B, C, D, SE, Form 4562, Form 6251, Form 8829, and Form 8949 pages, conditional on what the return actually needs (`skill/scripts/output/`).
5. Emits:
   - `form_1040.pdf`, `schedule_*.pdf`, `form_*.pdf` (federal filled forms)
   - `state_*.pdf` (per-state returns)
   - `result.json` (complete `CanonicalReturn` for year-over-year continuity)
   - `ffff_entries.json` and `ffff_entries.txt` (field-by-field transcript for Free File Fillable Forms)
   - `paper_bundle.pdf` (merged, page-ordered, signature-ready)

The skill does not transmit returns to the IRS. Individuals cannot talk to the IRS Modernized e-File (MeF) system directly. The skill produces artifacts you submit through an approved channel: Free File Fillable Forms, a state DOR portal, commercial software, or paper mail.

## Supported

- Federal + all 43 states with an income tax + DC
- Income: W-2 wages, 1099-INT/DIV/B/NEC/R/G, SSA-1099, Schedule C self-employment, Schedule E rental, Schedule K-1 passthroughs
- Filing statuses: Single, MFJ, MFS, HoH, QSS
- Prior-year ingestion: AcroForm, text-layer, and scanned PDFs (OCR via Azure Document Intelligence Unified US Tax model)
- OBBBA TY2025 changes: senior deduction, Schedule 1-A tips/overtime, Form 4547 Trump Account

## Install

The skill is a Python package. Clone the repo and install in a virtualenv:

```bash
git clone <repo> ~/Projects/Taxes
cd ~/Projects/Taxes
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

This installs the package declared in `pyproject.toml` plus runtime dependencies: `pypdf`, `pdfplumber`, `pdfrw`, `reportlab`, `pydantic`, `jsonschema`, `python-dateutil`, `tenforty` (OpenTaxSolver wrapper), and `azure-ai-documentintelligence`. It also wires the `tax-prep` console script.

For tests, install the dev extra:

```bash
pip install -e '.[dev]'
pytest skill/tests/
```

To use with Claude Code, symlink or copy `skill/` into your Claude skills directory:

```bash
ln -s ~/Projects/Taxes/skill ~/.claude/skills/tax-prep
```

Then invoke the skill from any Claude Code session — Claude reads `skill/SKILL.md` and drives the interview.

## CLI

The installed `tax-prep` script is a thin wrapper over `skill.scripts.pipeline.run_pipeline` (`skill/scripts/cli.py`). It is useful for scripted runs and for debugging without going through Claude.

```
tax-prep run --input <pdf_dir> --taxpayer-info <taxpayer.json> --output <out_dir> [--no-bundle] [--no-ffff]
tax-prep scan-email --output <pdf_dir> [--credentials <path>] [--tax-year 2025] [--run-pipeline]
tax-prep setup-gmail  # interactive Gmail API credential setup
tax-prep schema       # print the CanonicalReturn JSON schema on stdout
tax-prep version      # print the installed package version
```

Example:

```bash
tax-prep run \
    --input  ~/TaxData/alex/ty2025/documents \
    --taxpayer-info ~/TaxData/alex/ty2025/taxpayer_info.json \
    --output ~/TaxData/alex/ty2025/output
```

### Email scanning

Scan Gmail for tax document PDFs (W-2, 1099, 1098, 1095-A, SSA-1099, K-1) and download them automatically:

```bash
# One-time setup (creates Google Cloud project + OAuth2 credentials)
tax-prep setup-gmail

# Scan and download
tax-prep scan-email --output ./tax_pdfs

# Scan, download, and run the full pipeline in one command
tax-prep scan-email --output ./tax_pdfs --run-pipeline --taxpayer-info taxpayer.json
```

The setup wizard detects `gcloud` CLI and automates what it can. Without `gcloud`, it prints step-by-step instructions for the Google Cloud Console. OAuth2 tokens are cached at `~/.tax-prep/gmail_token.json` so subsequent scans are silent.

`taxpayer_info.json` is a partial `CanonicalReturn` dict. The minimal shape is:

```json
{
  "schema_version": "0.1.0",
  "tax_year": 2025,
  "filing_status": "single",
  "taxpayer": {
    "first_name": "Alex",
    "last_name": "Doe",
    "ssn": "111-22-3333",
    "date_of_birth": "1985-01-01"
  },
  "address": {
    "street1": "1 Test Lane",
    "city": "Springfield",
    "state": "IL",
    "zip": "62701",
    "country": "US"
  }
}
```

The full schema comes from `skill/scripts/models.py::CanonicalReturn` — use `tax-prep schema` to dump the current JSON schema. When the skill runs through Claude, the interview in `skill/SKILL.md` produces this file; for scripted runs you author it directly.

## Your data

Taxpayer data (SSNs, wages, account numbers, prior-year returns) lives in a directory you choose, never in this repo. The convention:

```
~/TaxData/<your-name>/ty2025/
  taxpayer_info.json   ← header fields (auth, status, address, dependents)
  documents/           ← source PDFs (W-2s, 1099s, prior-year return)
  output/              ← generated artifacts (filled PDFs, result.json, FFFF map, bundle)
```

Disk-level security (FileVault, encrypted external disk) is your responsibility.

## Architecture

See `ARCHITECTURE.md`. Short version: Claude drives the interview, Python scripts do the math, OCR handles messy prior-year PDFs, a state plugin API keeps per-state work independent. The deterministic back end is `skill/scripts/`; the Claude-facing interview prompt is `skill/SKILL.md`.

## Research foundation

Grounded in `skill/reference/ty2025-landscape.md` — a compiled reference of the TY2025 filing landscape (channels, schemas, key numbers, OBBBA changes, state reciprocity). Verified 2026-04-10.

## License

TBD. The `tenforty` dependency wraps OpenTaxSolver (GPL); GPL stays confined to the dependency graph, and our own code will be MIT or similar when a license is chosen.
