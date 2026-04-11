# Tax Prep Skill

A Claude Code skill that prepares US individual income tax returns (federal + all states) for Tax Year 2025 and forward. Designed to be installed once and run by each taxpayer against their own data.

**Status:** Under construction. This repository tracks the build; the skill is not yet deployable.

## What the skill does

Given your tax documents (W-2s, 1099s, prior-year return), it walks you through an interview, computes your return, and produces:

- **Filled IRS PDF forms** — 1040 plus every applicable schedule, ready to paper-file or review
- **Free File Fillable Forms (FFFF) entry table** — a structured map of every value to type into the IRS's free web-based federal e-file path
- **Per-state artifacts** — filled state forms plus whatever format your state's DOR accepts
- **Canonical return JSON** — a machine-readable record of your whole return for year-over-year continuity
- **Paper-file bundle** — merged, page-ordered, signature-ready PDF

It does **not** transmit returns to the IRS. Individuals cannot talk to the IRS Modernized e-File (MeF) system directly — that requires an authorized e-file provider. The skill produces artifacts you submit through an approved channel (FFFF, state portals, commercial software, or paper mail).

## What it supports

- **Jurisdiction:** US federal + all 43 states with an income tax + DC
- **Income types:** W-2 wages, 1099-INT/DIV/B (investments), Schedule C (self-employment), Schedule E (rental)
- **Filing statuses:** Single, Married Filing Jointly, Married Filing Separately, Head of Household, Qualifying Surviving Spouse
- **Prior-year data ingestion:** PDFs of filed returns (AcroForm, text-layer, or scanned — OCR is first-class), TXF exports from brokerages
- **Tax year:** TY2025 first, framework for future years

## Installation

Once the skill is ready:

```bash
git clone <repo> ~/Projects/Taxes
cd ~/Projects/Taxes
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp -r skill ~/.claude/skills/tax-prep
```

Then invoke from any Claude Code session with `/tax-prep`.

## Your data

Taxpayer data — SSNs, wages, account numbers, prior-year returns — lives in a directory you choose, never in this repo. The convention is:

```
~/TaxData/<your-name>/ty2025/
  return.json          ← canonical return, built up during the interview
  documents/           ← source PDFs (W-2s, 1099s, prior-year return)
  output/              ← generated artifacts
```

The skill validates this directory's canonical return against a JSON schema at every step. You own the disk-level security (FileVault, encrypted external disk, etc.).

## Running tests

```bash
pytest
```

All calculation modules are covered by golden-fixture tests: a reference taxpayer with hand-computed expected line-by-line output. Every calc change must keep the goldens green.

## Architecture

See `ARCHITECTURE.md` for the design. Short version: Claude drives the interview, Python scripts do the math, OCR handles messy prior-year PDFs, a state plugin API keeps per-state work independent. The skill is built via a short serial critical path followed by parallel sub-agent fan-out.

## Research foundation

Everything is grounded in `skill/reference/ty2025-landscape.md` — a compiled reference of the TY2025 filing landscape (channels, schemas, key numbers, OBBBA changes, state reciprocity, tool ecosystem). Verified 2026-04-10.

## License

TBD. The tenforty dependency (which wraps OpenTaxSolver, GPL) keeps GPL confined to the dependency graph; our own code will be MIT or similar.
