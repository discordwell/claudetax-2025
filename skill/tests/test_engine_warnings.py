"""Tests for engine-level diagnostic warnings (ComputedTotals.warnings).

The calc engine surfaces human-readable warnings whenever it makes a
simplifying assumption that could change the tax owed, so a silently wrong or
incomplete number never reaches the filer unflagged. Two cases are covered
today:

  1. **QBI above the §199A simplified threshold** — Form 8995-A governs the
     deduction but is out of scope (it needs SSTB classification, per-business
     W-2 wages, and UBIA the tool does not collect). The deduction shows as $0,
     which would overstate tax for filers who actually qualify, so the engine
     warns instead of dropping it silently.

  2. **Duplicated W-2 withholding** — when withholding is supplied on both the
     per-W-2 boxes and the ``payments`` aggregate, only the per-W-2 sum is used
     (the aggregate is ignored to avoid double-counting). The engine warns so
     the human can confirm the aggregate was a duplicate, not additional.

These warnings are merged into ``PipelineResult.warnings`` and printed by the
CLI.
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
    Payments,
    Person,
    ScheduleC,
    ScheduleCExpenses,
    W2,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _person() -> Person:
    return Person(
        first_name="Alex",
        last_name="Doe",
        ssn="111-22-3333",
        date_of_birth=dt.date(1985, 1, 1),
    )


def _spouse() -> Person:
    return Person(
        first_name="Pat",
        last_name="Doe",
        ssn="222-33-4444",
        date_of_birth=dt.date(1985, 1, 1),
    )


def _addr() -> Address:
    return Address(street1="1 Main St", city="Austin", state="TX", zip="78701")


def _base_return(
    filing_status: FilingStatus = FilingStatus.SINGLE, **overrides
) -> CanonicalReturn:
    defaults: dict = dict(
        tax_year=2025,
        filing_status=filing_status,
        taxpayer=_person(),
        address=_addr(),
    )
    if filing_status in (FilingStatus.MFJ, FilingStatus.MFS):
        defaults["spouse"] = _spouse()
    defaults.update(overrides)
    return CanonicalReturn(**defaults)  # type: ignore[arg-type]


def _schedule_c(gross: Decimal, expenses: Decimal = Decimal("0")) -> ScheduleC:
    return ScheduleC(
        business_name="Test Business",
        principal_business_or_profession="Consulting",
        line1_gross_receipts=gross,
        expenses=ScheduleCExpenses(line27a_other_expenses=expenses),
    )


def _has_qbi_warning(warnings: list[str]) -> bool:
    return any("QBI deduction shown as $0" in w for w in warnings)


def _has_double_withholding_warning(warnings: list[str]) -> bool:
    return any("W-2 federal withholding was supplied twice" in w for w in warnings)


# ---------------------------------------------------------------------------
# QBI above-threshold warning
# ---------------------------------------------------------------------------


class TestQBIAboveThresholdWarning:
    def test_above_threshold_single_emits_warning(self):
        """Single filer above $197,300 with positive QBI → warning, deduction $0."""
        ret = _base_return(schedules_c=[_schedule_c(Decimal("300000"))])
        c = compute(ret).computed
        assert c.qbi_deduction is None  # deduction dropped, as documented
        assert _has_qbi_warning(c.warnings)
        # The warning is actionable: it names Form 8995-A and the total QBI.
        msg = next(w for w in c.warnings if "QBI deduction shown as $0" in w)
        assert "Form 8995-A" in msg
        assert "300,000" in msg

    def test_above_threshold_mfj_emits_warning(self):
        """MFJ filer above $394,600 with positive QBI → warning."""
        ret = _base_return(
            filing_status=FilingStatus.MFJ,
            schedules_c=[_schedule_c(Decimal("600000"))],
        )
        c = compute(ret).computed
        assert c.qbi_deduction is None
        assert _has_qbi_warning(c.warnings)

    def test_below_threshold_no_warning(self):
        """Below the threshold the deduction is computed and no warning fires."""
        ret = _base_return(schedules_c=[_schedule_c(Decimal("45000"))])
        c = compute(ret).computed
        assert c.qbi_deduction is not None
        assert c.qbi_deduction > Decimal("0")
        assert not _has_qbi_warning(c.warnings)

    def test_above_threshold_but_no_positive_qbi_no_warning(self):
        """Above threshold from W-2 income but the only QBI source is a loss.

        ``_has_qbi_sources`` is True (a Schedule C exists) and TI exceeds the
        threshold, but total QBI is negative — there is no deduction to drop,
        so no warning should fire.
        """
        ret = _base_return(
            w2s=[
                W2(
                    employer_name="ACME",
                    box1_wages=Decimal("250000"),
                    box2_federal_income_tax_withheld=Decimal("50000"),
                )
            ],
            schedules_c=[_schedule_c(Decimal("10000"), expenses=Decimal("20000"))],
        )
        c = compute(ret).computed
        assert c.taxable_income is not None
        assert c.taxable_income > Decimal("197300")  # above the threshold
        assert not _has_qbi_warning(c.warnings)

    def test_w2_only_return_has_no_warnings(self):
        """A plain W-2 return makes no simplifying assumptions → empty warnings."""
        ret = _base_return(
            w2s=[
                W2(
                    employer_name="ACME",
                    box1_wages=Decimal("65000"),
                    box2_federal_income_tax_withheld=Decimal("10000"),
                )
            ]
        )
        c = compute(ret).computed
        assert c.warnings == []


# ---------------------------------------------------------------------------
# W-2 double-withholding warning
# ---------------------------------------------------------------------------


class TestW2DoubleWithholdingWarning:
    def test_both_sources_emits_warning(self):
        """W-2 box 2 AND the payments aggregate both set → double-entry warning."""
        ret = _base_return(
            w2s=[
                W2(
                    employer_name="ACME",
                    box1_wages=Decimal("80000"),
                    box2_federal_income_tax_withheld=Decimal("12000"),
                )
            ],
            payments=Payments(federal_income_tax_withheld_from_w2=Decimal("12000")),
        )
        c = compute(ret).computed
        assert _has_double_withholding_warning(c.warnings)

    def test_w2_box_only_no_warning(self):
        """Withholding only on the W-2 boxes (the normal case) → no warning."""
        ret = _base_return(
            w2s=[
                W2(
                    employer_name="ACME",
                    box1_wages=Decimal("80000"),
                    box2_federal_income_tax_withheld=Decimal("12000"),
                )
            ]
        )
        c = compute(ret).computed
        assert not _has_double_withholding_warning(c.warnings)

    def test_aggregate_only_no_warning(self):
        """Aggregate-only withholding (no per-W-2 boxes) is the documented
        fallback path, not a duplicate → no warning."""
        ret = _base_return(
            w2s=[
                W2(
                    employer_name="ACME",
                    box1_wages=Decimal("80000"),
                    box2_federal_income_tax_withheld=Decimal("0"),
                )
            ],
            payments=Payments(federal_income_tax_withheld_from_w2=Decimal("12000")),
        )
        c = compute(ret).computed
        assert not _has_double_withholding_warning(c.warnings)


# ---------------------------------------------------------------------------
# Pipeline surfacing
# ---------------------------------------------------------------------------


class TestPipelineSurfacesEngineWarnings:
    def test_qbi_warning_reaches_pipeline_result(self, tmp_path: Path):
        """Engine warnings are merged into PipelineResult.warnings."""
        from skill.scripts.pipeline import run_pipeline

        input_dir = tmp_path / "documents"
        input_dir.mkdir()
        output_dir = tmp_path / "output"

        taxpayer_info = {
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
                "street1": "1 Main St",
                "city": "Austin",
                "state": "TX",
                "zip": "78701",
                "country": "US",
            },
            "schedules_c": [
                {
                    "business_name": "Test Business",
                    "principal_business_or_profession": "Consulting",
                    "line1_gross_receipts": "300000",
                }
            ],
        }
        info_path = tmp_path / "taxpayer_info.json"
        info_path.write_text(json.dumps(taxpayer_info))

        # Disable rendering to keep the test fast; compute() always runs and is
        # the source of the warning under test.
        result = run_pipeline(
            input_dir=input_dir,
            taxpayer_info_path=info_path,
            output_dir=output_dir,
            render_form_1040=False,
            render_schedule_a=False,
            render_schedule_b=False,
            render_schedule_c=False,
            render_schedule_d=False,
            render_form_8949=False,
            render_schedule_se=False,
            render_form_6251=False,
            render_form_4562=False,
            render_form_8829=False,
            render_form_2441=False,
            render_form_8606=False,
            render_form_8863=False,
            render_form_8962=False,
            render_form_8995=False,
            render_form_4797=False,
            render_schedule_1=False,
            render_schedule_2=False,
            render_schedule_3=False,
            render_schedule_e=False,
            render_state_returns=False,
            build_paper_bundle=False,
            emit_ffff_map=False,
        )

        assert _has_qbi_warning(result.warnings)
        # And it round-trips into result.json via canonical_return.computed.
        assert _has_qbi_warning(result.canonical_return.computed.warnings)
