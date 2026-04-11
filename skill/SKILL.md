---
name: tax-prep
description: Use when a user wants help preparing their US individual income tax return (federal or state), amending a prior return, ingesting prior-year tax documents, or computing what they owe. Handles W-2 wages, 1099 investment income, Schedule C self-employment, and Schedule E rental. Outputs filled IRS PDFs, Free File Fillable Forms entries, and paper-file bundles.
---

# Tax Prep Skill

**Status:** Under construction. This is a stub. Interview flow will be written in the fan-out phase after the serial critical path completes.

## Overview

Prepares US individual income tax returns for TY2025 and forward. Claude drives a guided interview, deterministic Python scripts do the math, and the skill outputs a multi-channel delivery bundle (filled IRS PDFs, Free File Fillable Forms entry table, per-state artifacts, paper-file bundle).

**Design non-negotiables:**
- OBBBA-adjusted TY2025 numbers only (not Rev. Proc. 2024-40 alone).
- Canonical return JSON is the single source of truth; every module reads/writes it.
- Every calculation is covered by a golden fixture with a hand-computed answer key.
- Taxpayer data lives in a user-chosen directory outside this repo.
- OCR is first-class for prior-year document ingestion (Azure AI Document Intelligence Unified US Tax model).

**Design constraint:**
- The skill does NOT transmit returns to the IRS. Individuals cannot talk to MeF directly. The skill produces artifacts the user submits through an approved channel (FFFF, state DOR portals, commercial software, or paper).

## When to use

- User is preparing their own federal (or federal + state) income tax return for TY2025 or later.
- User wants to extract data from a prior-year tax return PDF.
- User is computing estimated taxes, checking refund/owed amounts, or modeling a what-if scenario.
- User is amending a prior-year return (Form 1040-X).

## When not to use

- Corporate (1120), partnership (1065), trust/estate (1041), or other non-individual returns.
- Tax advice or planning beyond mechanical computation.
- Years before TY2025 other than as prior-year source material for carryforwards.

## Reference material

- [TY2025 filing landscape research](reference/ty2025-landscape.md) — filing channels, MeF schemas, key numbers, OBBBA changes, state reciprocity.
- [TY2025 constants](reference/ty2025-constants.json) — brackets, deductions, phase-outs (*pending CP2*).
- [Canonical return schema](schemas/return.schema.json) — the data contract (*pending CP3*).

## Interview flow

*Pending fan-out phase. Will include: taxpayer identity → income docs → deductions → credits → state(s) → compute → output bundle.*
