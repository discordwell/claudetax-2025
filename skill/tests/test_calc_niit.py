"""Tests for the NIIT (Form 8960) patch.

NIIT = 3.8% x min(NII, MAGI - threshold), where thresholds are NOT indexed:
  Single/HoH/QSS  $200,000
  MFJ             $250,000
  MFS             $125,000

NII includes: interest, ordinary dividends, cap gain distributions, realized
cap gains (both ST and LT), passive rental income, royalties, non-retirement
annuities.

NII excludes: wages, SE income, 1099-R retirement distributions, SSA benefits,
tax-exempt interest, Section 121 primary residence gain.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from skill.scripts.calc.patches.niit import NIITResult, compute_niit
from skill.scripts.models import (
    Address,
    CanonicalReturn,
    FilingStatus,
    Form1099B,
    Form1099BTransaction,
    Form1099DIV,
    Form1099INT,
    Form1099R,
    FormSSA1099,
    Person,
    ScheduleE,
    ScheduleEProperty,
    W2,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _person(name: str = "Alex", ssn: str = "111-22-3333") -> Person:
    return Person(
        first_name=name, last_name="X", ssn=ssn, date_of_birth=dt.date(1985, 1, 1)
    )


def _addr() -> Address:
    return Address(street1="1 A", city="B", state="CA", zip="90001")


def _spouse(name: str = "Pat", ssn: str = "222-33-4444") -> Person:
    return Person(
        first_name=name, last_name="X", ssn=ssn, date_of_birth=dt.date(1985, 1, 1)
    )


def _base_return(filing_status: FilingStatus = FilingStatus.SINGLE, **overrides) -> CanonicalReturn:
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


# ---------------------------------------------------------------------------
# Threshold / base cases
# ---------------------------------------------------------------------------


class TestZeroNII:
    def test_zero_nii_zero_niit(self):
        ret = _base_return()
        result = compute_niit(ret, magi=Decimal("500000"))
        assert isinstance(result, NIITResult)
        assert result.net_investment_income == Decimal("0")
        assert result.niit == Decimal("0")
        assert result.tax_base == Decimal("0")

    def test_below_threshold_zero_niit(self):
        """MAGI below threshold: no NIIT even with NII."""
        ret = _base_return(
            forms_1099_int=[
                Form1099INT(payer_name="Bank", box1_interest_income=Decimal("50000"))
            ],
        )
        result = compute_niit(ret, magi=Decimal("150000"))
        # $150k MAGI < $200k single threshold -> excess = 0 -> niit = 0
        assert result.excess_magi_over_threshold == Decimal("0")
        assert result.tax_base == Decimal("0")
        assert result.niit == Decimal("0")
        # NII is still tracked
        assert result.net_investment_income == Decimal("50000")

    def test_magi_exactly_at_threshold(self):
        ret = _base_return(
            forms_1099_int=[
                Form1099INT(payer_name="Bank", box1_interest_income=Decimal("10000"))
            ],
        )
        result = compute_niit(ret, magi=Decimal("200000"))
        assert result.excess_magi_over_threshold == Decimal("0")
        assert result.niit == Decimal("0")


# ---------------------------------------------------------------------------
# Core calculation — filing status thresholds
# ---------------------------------------------------------------------------


class TestSingle:
    def test_single_250k_magi_30k_nii(self):
        """Single, $250k MAGI, $30k NII -> excess $50k, base $30k, NIIT $1,140."""
        ret = _base_return(
            forms_1099_int=[
                Form1099INT(payer_name="Bank", box1_interest_income=Decimal("30000"))
            ],
        )
        result = compute_niit(ret, magi=Decimal("250000"))
        assert result.threshold == Decimal("200000")
        assert result.excess_magi_over_threshold == Decimal("50000")
        assert result.net_investment_income == Decimal("30000")
        assert result.tax_base == Decimal("30000")
        assert result.niit == Decimal("1140.000")

    def test_single_500k_magi_30k_nii(self):
        """Single, $500k MAGI, $30k NII -> NII capped base, NIIT $1,140."""
        ret = _base_return(
            forms_1099_int=[
                Form1099INT(payer_name="Bank", box1_interest_income=Decimal("30000"))
            ],
        )
        result = compute_niit(ret, magi=Decimal("500000"))
        assert result.excess_magi_over_threshold == Decimal("300000")
        assert result.tax_base == Decimal("30000")  # capped at NII
        assert result.niit == Decimal("1140.000")

    def test_single_220k_magi_50k_nii(self):
        """Single, $220k MAGI, $50k NII -> excess caps at $20k, base $20k, NIIT $760."""
        ret = _base_return(
            forms_1099_int=[
                Form1099INT(payer_name="Bank", box1_interest_income=Decimal("50000"))
            ],
        )
        result = compute_niit(ret, magi=Decimal("220000"))
        assert result.excess_magi_over_threshold == Decimal("20000")
        assert result.tax_base == Decimal("20000")  # capped at excess
        assert result.niit == Decimal("760.000")


class TestMFJ:
    def test_mfj_260k_magi_20k_nii(self):
        """MFJ, $260k MAGI, $20k NII -> excess $10k, base $10k, NIIT $380."""
        ret = _base_return(
            filing_status=FilingStatus.MFJ,
            forms_1099_int=[
                Form1099INT(payer_name="Bank", box1_interest_income=Decimal("20000"))
            ],
        )
        result = compute_niit(ret, magi=Decimal("260000"))
        assert result.threshold == Decimal("250000")
        assert result.excess_magi_over_threshold == Decimal("10000")
        assert result.tax_base == Decimal("10000")
        assert result.niit == Decimal("380.000")


class TestMFS:
    def test_mfs_130k_magi_15k_nii(self):
        """MFS, $130k MAGI, $15k NII -> excess $5k, base $5k, NIIT $190."""
        ret = _base_return(
            filing_status=FilingStatus.MFS,
            forms_1099_int=[
                Form1099INT(payer_name="Bank", box1_interest_income=Decimal("15000"))
            ],
        )
        result = compute_niit(ret, magi=Decimal("130000"))
        assert result.threshold == Decimal("125000")
        assert result.excess_magi_over_threshold == Decimal("5000")
        assert result.tax_base == Decimal("5000")
        assert result.niit == Decimal("190.000")


class TestHoH:
    def test_hoh_uses_200k_threshold(self):
        ret = _base_return(
            filing_status=FilingStatus.HOH,
            forms_1099_int=[
                Form1099INT(payer_name="Bank", box1_interest_income=Decimal("10000"))
            ],
        )
        result = compute_niit(ret, magi=Decimal("210000"))
        assert result.threshold == Decimal("200000")
        assert result.excess_magi_over_threshold == Decimal("10000")
        assert result.niit == Decimal("380.000")


# ---------------------------------------------------------------------------
# Income source coverage
# ---------------------------------------------------------------------------


class TestNIIIncludes:
    def test_interest_plus_dividends_plus_ltcg_plus_rental(self):
        """Combined test: verify all major NII sources sum correctly."""
        ret = _base_return(
            forms_1099_int=[
                Form1099INT(payer_name="Bank", box1_interest_income=Decimal("5000"))
            ],
            forms_1099_div=[
                Form1099DIV(
                    payer_name="Fund",
                    box1a_ordinary_dividends=Decimal("3000"),
                    box2a_total_capital_gain_distributions=Decimal("2000"),
                )
            ],
            forms_1099_b=[
                Form1099B(
                    broker_name="Broker",
                    transactions=[
                        Form1099BTransaction(
                            description="AAPL",
                            date_sold=dt.date(2025, 6, 1),
                            date_acquired=dt.date(2020, 1, 1),
                            proceeds=Decimal("20000"),
                            cost_basis=Decimal("12000"),
                            is_long_term=True,
                        ),
                        Form1099BTransaction(
                            description="TSLA",
                            date_sold=dt.date(2025, 6, 1),
                            date_acquired=dt.date(2025, 2, 1),
                            proceeds=Decimal("7000"),
                            cost_basis=Decimal("5000"),
                            is_long_term=False,
                        ),
                    ],
                )
            ],
            schedules_e=[
                ScheduleE(
                    properties=[
                        ScheduleEProperty(
                            address=_addr(),
                            rents_received=Decimal("15000"),
                            repairs=Decimal("5000"),
                        )
                    ]
                )
            ],
        )
        # NII components:
        #  interest            5,000
        #  ordinary dividends  3,000
        #  cap gain distr      2,000
        #  LT cap gain         8,000 (20000 - 12000)
        #  ST cap gain         2,000 (7000 - 5000)
        #  rental net         10,000 (15000 - 5000)
        # Total NII =         30,000
        result = compute_niit(ret, magi=Decimal("250000"))
        assert result.net_investment_income == Decimal("30000")
        # excess = 50,000, tax_base = min(30k, 50k) = 30k
        assert result.tax_base == Decimal("30000")
        assert result.niit == Decimal("1140.000")
        # Per-source breakdown exposed
        assert "interest" in result.details
        assert "ordinary_dividends" in result.details
        assert "capital_gain_distributions" in result.details
        assert "realized_capital_gains" in result.details
        assert "rental_net" in result.details

    def test_royalties_counted(self):
        """Royalties on Schedule E are NII (Part I royalties)."""
        ret = _base_return(
            schedules_e=[
                ScheduleE(
                    properties=[
                        ScheduleEProperty(
                            address=_addr(),
                            rents_received=Decimal("0"),
                            royalties_received=Decimal("10000"),
                        )
                    ]
                )
            ],
        )
        result = compute_niit(ret, magi=Decimal("250000"))
        # Sch E property net = 0 + 10000 - 0 = 10000, all from royalties
        assert result.net_investment_income == Decimal("10000")

    def test_rental_loss_reduces_nii(self):
        """A rental loss reduces NII (v1 simplification)."""
        ret = _base_return(
            forms_1099_int=[
                Form1099INT(payer_name="Bank", box1_interest_income=Decimal("20000"))
            ],
            schedules_e=[
                ScheduleE(
                    properties=[
                        ScheduleEProperty(
                            address=_addr(),
                            rents_received=Decimal("10000"),
                            repairs=Decimal("15000"),  # 5k loss
                        )
                    ]
                )
            ],
        )
        result = compute_niit(ret, magi=Decimal("250000"))
        # NII = 20,000 - 5,000 = 15,000
        assert result.net_investment_income == Decimal("15000")

    def test_st_and_lt_gains_both_counted(self):
        ret = _base_return(
            forms_1099_b=[
                Form1099B(
                    broker_name="Broker",
                    transactions=[
                        Form1099BTransaction(
                            description="LT",
                            date_sold=dt.date(2025, 6, 1),
                            date_acquired=dt.date(2020, 1, 1),
                            proceeds=Decimal("10000"),
                            cost_basis=Decimal("4000"),
                            is_long_term=True,
                        ),
                        Form1099BTransaction(
                            description="ST",
                            date_sold=dt.date(2025, 6, 1),
                            date_acquired=dt.date(2025, 3, 1),
                            proceeds=Decimal("5000"),
                            cost_basis=Decimal("2000"),
                            is_long_term=False,
                        ),
                    ],
                )
            ]
        )
        result = compute_niit(ret, magi=Decimal("250000"))
        # LT gain 6000 + ST gain 3000 = 9000
        assert result.net_investment_income == Decimal("9000")

    def test_capital_gain_with_adjustment(self):
        """Adjustment amounts (wash sales, etc.) flow into the gain."""
        ret = _base_return(
            forms_1099_b=[
                Form1099B(
                    broker_name="Broker",
                    transactions=[
                        Form1099BTransaction(
                            description="X",
                            date_sold=dt.date(2025, 6, 1),
                            date_acquired=dt.date(2020, 1, 1),
                            proceeds=Decimal("10000"),
                            cost_basis=Decimal("8000"),
                            adjustment_amount=Decimal("500"),
                            is_long_term=True,
                        )
                    ],
                )
            ]
        )
        result = compute_niit(ret, magi=Decimal("250000"))
        # Gain = 10000 - 8000 + 500 = 2500
        assert result.net_investment_income == Decimal("2500")


class TestNIIExcludes:
    def test_wages_not_counted(self):
        ret = _base_return(
            w2s=[W2(employer_name="A", box1_wages=Decimal("500000"))],
        )
        result = compute_niit(ret, magi=Decimal("500000"))
        assert result.net_investment_income == Decimal("0")
        assert result.niit == Decimal("0")

    def test_tax_exempt_interest_not_counted(self):
        """Box 8 (tax-exempt) interest is NOT NII. Only box 1 counts."""
        ret = _base_return(
            forms_1099_int=[
                Form1099INT(
                    payer_name="Muni",
                    box1_interest_income=Decimal("0"),
                    box8_tax_exempt_interest=Decimal("50000"),
                )
            ],
        )
        result = compute_niit(ret, magi=Decimal("500000"))
        assert result.net_investment_income == Decimal("0")
        assert result.niit == Decimal("0")

    def test_1099r_retirement_distribution_not_counted(self):
        """Retirement distributions are NOT NII."""
        ret = _base_return(
            forms_1099_r=[
                Form1099R(
                    payer_name="Pension Co",
                    box1_gross_distribution=Decimal("60000"),
                    box2a_taxable_amount=Decimal("60000"),
                )
            ],
        )
        result = compute_niit(ret, magi=Decimal("500000"))
        assert result.net_investment_income == Decimal("0")
        assert result.niit == Decimal("0")

    def test_ssa_benefits_not_counted(self):
        ret = _base_return(
            forms_ssa_1099=[
                FormSSA1099(box5_net_benefits=Decimal("30000"))
            ],
        )
        result = compute_niit(ret, magi=Decimal("500000"))
        assert result.net_investment_income == Decimal("0")
        assert result.niit == Decimal("0")


# ---------------------------------------------------------------------------
# Result dataclass contract
# ---------------------------------------------------------------------------


class TestNIITResultShape:
    def test_result_has_required_fields(self):
        ret = _base_return(
            forms_1099_int=[
                Form1099INT(payer_name="Bank", box1_interest_income=Decimal("10000"))
            ],
        )
        result = compute_niit(ret, magi=Decimal("250000"))
        assert isinstance(result.net_investment_income, Decimal)
        assert isinstance(result.magi, Decimal)
        assert isinstance(result.threshold, Decimal)
        assert isinstance(result.excess_magi_over_threshold, Decimal)
        assert isinstance(result.tax_base, Decimal)
        assert isinstance(result.niit, Decimal)
        assert isinstance(result.details, dict)
        assert result.magi == Decimal("250000")

    def test_result_is_frozen(self):
        """NIITResult is a frozen dataclass — immutable."""
        ret = _base_return()
        result = compute_niit(ret, magi=Decimal("0"))
        import dataclasses as dc
        assert dc.is_dataclass(result)
        # frozen dataclasses raise on attribute set
        import pytest
        with pytest.raises(dc.FrozenInstanceError):
            result.niit = Decimal("999")  # type: ignore[misc]
