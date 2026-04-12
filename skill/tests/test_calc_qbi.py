"""Tests for the QBI (Section 199A) deduction patch — Form 8995 simplified.

Section 199A allows a deduction of up to 20% of qualified business income
from pass-through entities. For TY2025, Form 8995 (simplified) is used when
taxable income before QBI <= $197,300 (Single) / $394,600 (MFJ).

Deduction = min(20% of total QBI, 20% of taxable income before QBI)

QBI sources:
  - Schedule C net profit
  - Schedule E net rental (qbi_qualified=True properties only)
  - K-1 ordinary business income (qbi_qualified=True)

QBI excludes:
  - W-2 wages
  - Interest, dividends, capital gains
  - Guaranteed payments from partnerships
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from skill.scripts.calc.patches.qbi import QBIResult, compute_qbi
from skill.scripts.calc.engine import compute
from skill.scripts.models import (
    Address,
    CanonicalReturn,
    FilingStatus,
    Person,
    ScheduleC,
    ScheduleCExpenses,
    ScheduleE,
    ScheduleEProperty,
    ScheduleK1,
    W2,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _person(name: str = "Alex", ssn: str = "111-22-3333") -> Person:
    return Person(
        first_name=name, last_name="Doe", ssn=ssn,
        date_of_birth=dt.date(1985, 1, 1),
    )


def _addr() -> Address:
    return Address(street1="1 Main St", city="Austin", state="TX", zip="78701")


def _spouse(name: str = "Pat", ssn: str = "222-33-4444") -> Person:
    return Person(
        first_name=name, last_name="Doe", ssn=ssn,
        date_of_birth=dt.date(1985, 1, 1),
    )


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
        expenses=ScheduleCExpenses(
            line27a_other_expenses=expenses,
        ),
    )


def _schedule_e_prop(
    rents: Decimal,
    expenses: Decimal = Decimal("0"),
    qbi_qualified: bool = False,
) -> ScheduleEProperty:
    return ScheduleEProperty(
        address=_addr(),
        rents_received=rents,
        taxes=expenses,
        qbi_qualified=qbi_qualified,
    )


# ---------------------------------------------------------------------------
# Schedule C QBI
# ---------------------------------------------------------------------------


class TestScheduleCQBI:
    def test_schedule_c_45k_gives_9k_deduction(self):
        """Schedule C net profit of $45K → QBI deduction = $9K (20%)."""
        ret = _base_return(
            schedules_c=[_schedule_c(Decimal("45000"))],
        )
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("45000"))
        assert result.total_qbi == Decimal("45000.00")
        assert result.schedule_c_qbi == Decimal("45000.00")
        assert result.twenty_pct_of_qbi == Decimal("9000.00")
        assert result.qbi_deduction == Decimal("9000.00")
        assert result.simplified_eligible is True

    def test_schedule_c_with_expenses(self):
        """Schedule C gross $60K - $15K expenses = $45K net → QBI = $9K."""
        ret = _base_return(
            schedules_c=[
                _schedule_c(Decimal("60000"), expenses=Decimal("15000"))
            ],
        )
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("45000"))
        assert result.total_qbi == Decimal("45000.00")
        assert result.qbi_deduction == Decimal("9000.00")

    def test_multiple_schedule_c_summed(self):
        """Two Schedule Cs: $30K + $20K = $50K total QBI → $10K deduction."""
        ret = _base_return(
            schedules_c=[
                _schedule_c(Decimal("30000")),
                _schedule_c(Decimal("20000")),
            ],
        )
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("100000"))
        assert result.total_qbi == Decimal("50000.00")
        assert result.qbi_deduction == Decimal("10000.00")


# ---------------------------------------------------------------------------
# Schedule E QBI
# ---------------------------------------------------------------------------


class TestScheduleEQBI:
    def test_schedule_e_qbi_qualified_10k(self):
        """Schedule E rental with qbi_qualified=True, $10K net → $2K deduction."""
        ret = _base_return(
            schedules_e=[
                ScheduleE(
                    properties=[
                        _schedule_e_prop(
                            Decimal("10000"), qbi_qualified=True
                        ),
                    ]
                )
            ],
        )
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("50000"))
        assert result.total_qbi == Decimal("10000.00")
        assert result.schedule_e_qbi == Decimal("10000.00")
        assert result.qbi_deduction == Decimal("2000.00")

    def test_schedule_e_not_qualified_excluded(self):
        """Schedule E rental with qbi_qualified=False → no QBI."""
        ret = _base_return(
            schedules_e=[
                ScheduleE(
                    properties=[
                        _schedule_e_prop(
                            Decimal("10000"), qbi_qualified=False
                        ),
                    ]
                )
            ],
        )
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("50000"))
        assert result.total_qbi == Decimal("0.00")
        assert result.schedule_e_qbi == Decimal("0.00")
        assert result.qbi_deduction == Decimal("0")

    def test_schedule_e_mixed_qualified_and_not(self):
        """Only qbi_qualified properties contribute to QBI."""
        ret = _base_return(
            schedules_e=[
                ScheduleE(
                    properties=[
                        _schedule_e_prop(
                            Decimal("10000"), qbi_qualified=True
                        ),
                        _schedule_e_prop(
                            Decimal("20000"), qbi_qualified=False
                        ),
                    ]
                )
            ],
        )
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("50000"))
        assert result.total_qbi == Decimal("10000.00")
        assert result.schedule_e_qbi == Decimal("10000.00")
        assert result.qbi_deduction == Decimal("2000.00")


# ---------------------------------------------------------------------------
# K-1 QBI
# ---------------------------------------------------------------------------


class TestK1QBI:
    def test_k1_qbi_qualified(self):
        """K-1 ordinary business income with qbi_qualified=True."""
        ret = _base_return(
            schedules_k1=[
                ScheduleK1(
                    source_name="ABC Partnership",
                    ordinary_business_income=Decimal("25000"),
                    qbi_qualified=True,
                ),
            ],
        )
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("50000"))
        assert result.total_qbi == Decimal("25000.00")
        assert result.k1_qbi == Decimal("25000.00")
        assert result.qbi_deduction == Decimal("5000.00")

    def test_k1_not_qualified_excluded(self):
        """K-1 with qbi_qualified=False → no QBI."""
        ret = _base_return(
            schedules_k1=[
                ScheduleK1(
                    source_name="XYZ Corp",
                    ordinary_business_income=Decimal("25000"),
                    qbi_qualified=False,
                ),
            ],
        )
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("50000"))
        assert result.total_qbi == Decimal("0.00")
        assert result.qbi_deduction == Decimal("0")


# ---------------------------------------------------------------------------
# TI cap (20% of taxable income)
# ---------------------------------------------------------------------------


class TestTICap:
    def test_cap_at_20pct_of_ti(self):
        """When 20% of TI < 20% of QBI, cap applies.

        QBI = $100K → 20% = $20K
        TI = $50K → 20% = $10K
        Deduction = $10K (capped)
        """
        ret = _base_return(
            schedules_c=[_schedule_c(Decimal("100000"))],
        )
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("50000"))
        assert result.twenty_pct_of_qbi == Decimal("20000.00")
        assert result.twenty_pct_of_ti == Decimal("10000.00")
        assert result.qbi_deduction == Decimal("10000.00")

    def test_no_cap_when_ti_is_large(self):
        """When 20% of TI > 20% of QBI, no cap.

        QBI = $50K → 20% = $10K
        TI = $100K → 20% = $20K
        Deduction = $10K (QBI wins, no cap)
        """
        ret = _base_return(
            schedules_c=[_schedule_c(Decimal("50000"))],
        )
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("100000"))
        assert result.twenty_pct_of_qbi == Decimal("10000.00")
        assert result.twenty_pct_of_ti == Decimal("20000.00")
        assert result.qbi_deduction == Decimal("10000.00")

    def test_zero_ti_means_zero_deduction(self):
        """When TI before QBI is zero, deduction is zero."""
        ret = _base_return(
            schedules_c=[_schedule_c(Decimal("50000"))],
        )
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("0"))
        assert result.qbi_deduction == Decimal("0")
        # But total_qbi is still tracked
        assert result.total_qbi == Decimal("50000.00")

    def test_negative_ti_means_zero_deduction(self):
        """When TI before QBI is negative, deduction is zero."""
        ret = _base_return(
            schedules_c=[_schedule_c(Decimal("50000"))],
        )
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("-5000"))
        assert result.qbi_deduction == Decimal("0")


# ---------------------------------------------------------------------------
# W-2 only — no QBI
# ---------------------------------------------------------------------------


class TestW2OnlyNoQBI:
    def test_w2_only_no_qbi(self):
        """W-2 income only: no Schedule C/E/K-1 → no QBI sources."""
        ret = _base_return(
            w2s=[
                W2(
                    employer_name="ACME Corp",
                    box1_wages=Decimal("100000"),
                    box2_federal_income_tax_withheld=Decimal("20000"),
                ),
            ],
        )
        # The engine won't even call compute_qbi for W-2-only returns.
        # But we can test the patch directly.
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("84250"))
        assert result.total_qbi == Decimal("0.00")
        assert result.qbi_deduction == Decimal("0")

    def test_engine_w2_only_no_qbi_on_computed(self):
        """W-2-only return through the full engine: qbi_deduction is None."""
        ret = _base_return(
            w2s=[
                W2(
                    employer_name="ACME Corp",
                    box1_wages=Decimal("65000"),
                    box2_federal_income_tax_withheld=Decimal("10000"),
                ),
            ],
        )
        result = compute(ret)
        assert result.computed.qbi_deduction is None


# ---------------------------------------------------------------------------
# Threshold gate (simplified vs 8995-A)
# ---------------------------------------------------------------------------


class TestThresholdGate:
    def test_single_below_threshold_simplified(self):
        """TI = $100K < $197,300 single threshold → simplified eligible."""
        ret = _base_return(
            schedules_c=[_schedule_c(Decimal("100000"))],
        )
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("100000"))
        assert result.simplified_eligible is True
        assert result.qbi_deduction == Decimal("20000.00")

    def test_single_at_threshold_simplified(self):
        """TI = $197,300 exactly → still simplified eligible."""
        ret = _base_return(
            schedules_c=[_schedule_c(Decimal("200000"))],
        )
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("197300"))
        assert result.simplified_eligible is True
        assert result.qbi_deduction > Decimal("0")

    def test_single_above_threshold_not_simplified(self):
        """TI = $200,000 > $197,300 single threshold → not simplified.

        Form 8995-A is required (not implemented), deduction = $0.
        """
        ret = _base_return(
            schedules_c=[_schedule_c(Decimal("200000"))],
        )
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("200000"))
        assert result.simplified_eligible is False
        assert result.qbi_deduction == Decimal("0")

    def test_mfj_below_threshold_simplified(self):
        """MFJ TI = $300K < $394,600 threshold → simplified eligible."""
        ret = _base_return(
            filing_status=FilingStatus.MFJ,
            schedules_c=[_schedule_c(Decimal("300000"))],
        )
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("300000"))
        assert result.simplified_eligible is True
        assert result.qbi_deduction == Decimal("60000.00")

    def test_mfj_above_threshold_not_simplified(self):
        """MFJ TI = $400K > $394,600 threshold → not simplified."""
        ret = _base_return(
            filing_status=FilingStatus.MFJ,
            schedules_c=[_schedule_c(Decimal("400000"))],
        )
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("400000"))
        assert result.simplified_eligible is False
        assert result.qbi_deduction == Decimal("0")


# ---------------------------------------------------------------------------
# Net QBI loss
# ---------------------------------------------------------------------------


class TestNetQBILoss:
    def test_negative_qbi_zero_deduction(self):
        """Schedule C net loss → total_qbi < 0 → deduction = $0."""
        ret = _base_return(
            schedules_c=[
                _schedule_c(Decimal("10000"), expenses=Decimal("20000")),
            ],
        )
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("50000"))
        assert result.total_qbi == Decimal("-10000.00")
        assert result.qbi_deduction == Decimal("0")


# ---------------------------------------------------------------------------
# Mixed sources (Schedule C + E + K-1)
# ---------------------------------------------------------------------------


class TestMixedSources:
    def test_all_three_sources(self):
        """Schedule C + qualified E + qualified K-1 all contribute."""
        ret = _base_return(
            schedules_c=[_schedule_c(Decimal("30000"))],
            schedules_e=[
                ScheduleE(
                    properties=[
                        _schedule_e_prop(Decimal("10000"), qbi_qualified=True),
                    ]
                )
            ],
            schedules_k1=[
                ScheduleK1(
                    source_name="Partnership",
                    ordinary_business_income=Decimal("10000"),
                    qbi_qualified=True,
                ),
            ],
        )
        result = compute_qbi(ret, taxable_income_before_qbi=Decimal("100000"))
        assert result.schedule_c_qbi == Decimal("30000.00")
        assert result.schedule_e_qbi == Decimal("10000.00")
        assert result.k1_qbi == Decimal("10000.00")
        assert result.total_qbi == Decimal("50000.00")
        assert result.qbi_deduction == Decimal("10000.00")


# ---------------------------------------------------------------------------
# Engine integration
# ---------------------------------------------------------------------------


class TestEngineIntegration:
    def test_schedule_c_qbi_reduces_taxable_income(self):
        """Schedule C QBI deduction flows through the engine and reduces TI."""
        ret = _base_return(
            schedules_c=[_schedule_c(Decimal("45000"))],
        )
        result = compute(ret)
        c = result.computed
        assert c.qbi_deduction is not None
        assert c.qbi_deduction > Decimal("0")
        # QBI deduction should be separate from deduction_taken (std/itemized)
        # and should reduce TI compared to what it would be without QBI
        assert c.taxable_income is not None
        assert c.adjusted_gross_income is not None
        assert c.deduction_taken is not None
        # TI = AGI - deduction_taken - qbi_deduction (approximately,
        # may differ by small rounding from tenforty's bracket tables)
        expected_ti_approx = (
            c.adjusted_gross_income - c.deduction_taken - c.qbi_deduction
        )
        assert abs(c.taxable_income - expected_ti_approx) < Decimal("5.00")


# ---------------------------------------------------------------------------
# Form 8995 renderer
# ---------------------------------------------------------------------------


class TestForm8995Render:
    def test_form_8995_fields_computed(self):
        """Form 8995 fields are populated correctly for a Schedule C return."""
        from skill.scripts.output.form_8995 import compute_form_8995_fields

        ret = _base_return(
            schedules_c=[_schedule_c(Decimal("45000"))],
        )
        result = compute(ret)
        fields = compute_form_8995_fields(result)

        assert fields.taxpayer_name == "Alex Doe"
        assert len(fields.business_entries) == 1
        assert fields.business_entries[0].business_name == "Test Business"
        assert fields.line_16_total_deduction > Decimal("0")

    def test_form_8995_render_creates_pdf(self, tmp_path):
        """Form 8995 PDF is created without error."""
        from skill.scripts.output.form_8995 import (
            compute_form_8995_fields,
            render_form_8995_pdf,
        )

        ret = _base_return(
            schedules_c=[_schedule_c(Decimal("45000"))],
        )
        result = compute(ret)
        fields = compute_form_8995_fields(result)
        pdf_path = tmp_path / "form_8995.pdf"
        render_form_8995_pdf(fields, pdf_path)
        assert pdf_path.exists()
        assert pdf_path.stat().st_size > 0

    def test_form_8995_required_gate(self):
        """form_8995_required returns True for QBI returns, False for W-2 only."""
        from skill.scripts.output.form_8995 import form_8995_required

        # With Schedule C → QBI → required
        ret_sc = _base_return(
            schedules_c=[_schedule_c(Decimal("45000"))],
        )
        result_sc = compute(ret_sc)
        assert form_8995_required(result_sc) is True

        # W-2 only → no QBI → not required
        ret_w2 = _base_return(
            w2s=[
                W2(
                    employer_name="ACME",
                    box1_wages=Decimal("65000"),
                    box2_federal_income_tax_withheld=Decimal("10000"),
                ),
            ],
        )
        result_w2 = compute(ret_w2)
        assert form_8995_required(result_w2) is False
