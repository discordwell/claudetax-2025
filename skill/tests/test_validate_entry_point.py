"""Tests for the top-level `skill.scripts.validate.run_return_validation`
entry point and its integration with `engine.compute()`.

The wave-3 cleanup series (S2) introduced:

  1. `skill.scripts.validate.run_return_validation(return_) -> dict` which
     wraps every validation pass the skill runs on a CanonicalReturn into
     a single JSON-serializable dict.
  2. A new `validation_report: dict[str, Any] | None` field on
     `ComputedTotals` that stores the report from (1) so downstream
     consumers (SKILL.md interview, output bundlers, channel selector)
     can read it without re-running the checks.

These tests lock:
  - Entry-point shape (top-level `ffff` key, known sub-keys).
  - Engine wiring (compute() populates `validation_report`).
  - FFFF blocker surfaces in the report for a blocker-triggering return.
  - Bit-for-bit preservation of simple_w2_standard computed totals
    (the new field must not alter any existing numeric field).
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

from skill.scripts.calc.engine import compute
from skill.scripts.models import (
    Address,
    CanonicalReturn,
    FilingStatus,
    Person,
    ScheduleK1,
    W2,
)
from skill.scripts.validate import run_return_validation


def _addr() -> Address:
    return Address(street1="1 Test Lane", city="Springfield", state="IL", zip="62701")


def _person(dob: dt.date = dt.date(1985, 5, 5)) -> Person:
    return Person(
        first_name="Alex",
        last_name="Doe",
        ssn="111-22-3333",
        date_of_birth=dob,
    )


def _simple_return() -> CanonicalReturn:
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=_person(),
        address=_addr(),
        w2s=[
            W2(
                employer_name="Acme",
                box1_wages=Decimal("65000.00"),
                box2_federal_income_tax_withheld=Decimal("7500.00"),
            )
        ],
    )


class TestRunReturnValidationShape:
    """Report shape is stable: top-level `ffff` key, known sub-keys."""

    def test_report_has_ffff_key(self):
        report = run_return_validation(_simple_return())
        assert "ffff" in report

    def test_ffff_sub_keys_present(self):
        report = run_return_validation(_simple_return())
        ffff = report["ffff"]
        assert set(ffff.keys()) >= {
            "compatible",
            "blockers",
            "warnings",
            "infos",
            "details",
        }

    def test_compatible_is_bool(self):
        report = run_return_validation(_simple_return())
        assert isinstance(report["ffff"]["compatible"], bool)

    def test_lists_are_json_serializable(self):
        """The whole report must round-trip through json.dumps — this is
        how it lands on ComputedTotals.validation_report."""
        report = run_return_validation(_simple_return())
        dumped = json.dumps(report)
        round_tripped = json.loads(dumped)
        assert round_tripped["ffff"]["compatible"] is True

    def test_simple_return_is_ffff_compatible(self):
        report = run_return_validation(_simple_return())
        assert report["ffff"]["compatible"] is True
        assert report["ffff"]["blockers"] == []


class TestEngineComputeStoresValidationReport:
    """`engine.compute()` populates ComputedTotals.validation_report."""

    def test_validation_report_is_populated(self):
        r = compute(_simple_return())
        assert r.computed.validation_report is not None
        assert "ffff" in r.computed.validation_report

    def test_validation_report_matches_standalone_call(self):
        """The report stored by compute() should match running the
        validator directly against the patched return. This proves the
        engine runs the validator AFTER patches (important for Form 4547
        forcing trump_account_deduction_form_4547 to $0)."""
        r = compute(_simple_return())
        standalone = run_return_validation(r)
        assert r.computed.validation_report == standalone

    def test_computed_totals_numeric_fields_unchanged(self):
        """Adding the new field must not alter any existing numeric
        field on ComputedTotals — regression-lock the simple_w2_standard
        baseline."""
        r = compute(_simple_return())
        assert r.computed.adjusted_gross_income == Decimal("65000.00")
        assert r.computed.taxable_income == Decimal("49250.00")
        assert r.computed.tentative_tax == Decimal("5755.00")
        assert r.computed.total_tax == Decimal("5755.00")
        assert r.computed.refund == Decimal("1745.00")


class TestFFFFBlockerSurfacesInReport:
    """A return with an FFFF-disqualifying condition (Schedule K-1 presence)
    must produce blockers = [...] in the report and `compatible=False`.
    K-1 is the cheapest blocker to trigger without bloating the fixture.
    """

    def _return_with_k1(self) -> CanonicalReturn:
        return CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=_person(),
            address=_addr(),
            w2s=[
                W2(
                    employer_name="Acme",
                    box1_wages=Decimal("65000.00"),
                    box2_federal_income_tax_withheld=Decimal("7500.00"),
                )
            ],
            schedules_k1=[
                ScheduleK1(
                    source_name="Partnership LLC",
                    source_ein="12-3456789",
                    source_type="partnership",
                )
            ],
        )

    def test_blocker_present(self):
        report = run_return_validation(self._return_with_k1())
        assert report["ffff"]["compatible"] is False
        blockers = report["ffff"]["blockers"]
        assert any(b["code"] == "FFFF_UNSUPPORTED_FORM_SCHEDULE_K1" for b in blockers)

    def test_compute_stores_blocker(self):
        """`engine.compute()` must surface the blocker on ComputedTotals."""
        r = compute(self._return_with_k1())
        assert r.computed.validation_report is not None
        ffff = r.computed.validation_report["ffff"]
        assert ffff["compatible"] is False
        codes = {b["code"] for b in ffff["blockers"]}
        assert "FFFF_UNSUPPORTED_FORM_SCHEDULE_K1" in codes
