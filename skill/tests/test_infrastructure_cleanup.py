"""Tests for infrastructure cleanup tasks (wave 8E).

Task 1: Form 8949 pipeline gate — render_form_8949 parameter.
Task 2: Constants migration — EITC/CTC constants from JSON.
Task 3: K-1 Box 14 SE earnings flow to Schedule SE.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from skill.scripts.calc import constants as C
from skill.scripts.calc.engine import compute, earned_income
from skill.scripts.models import (
    Address,
    CanonicalReturn,
    FilingStatus,
    Person,
    ScheduleK1,
    W2,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _person(
    first: str = "Pat",
    last: str = "Taxpayer",
    ssn: str = "123-45-6789",
    dob: dt.date = dt.date(1985, 6, 15),
) -> Person:
    return Person(first_name=first, last_name=last, ssn=ssn, date_of_birth=dob)


def _addr() -> Address:
    return Address(street1="1 Main St", city="Anywhere", state="CA", zip="90001")


def _base_return(**overrides) -> CanonicalReturn:
    defaults = dict(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=_person(),
        address=_addr(),
    )
    defaults.update(overrides)
    return CanonicalReturn(**defaults)  # type: ignore[arg-type]


# ===========================================================================
# Task 1: Form 8949 pipeline gate
# ===========================================================================


class TestForm8949PipelineGate:
    """The render_form_8949 parameter should exist and be accepted."""

    def test_render_form_8949_parameter_exists(self):
        """run_pipeline should accept render_form_8949 without TypeError."""
        import inspect

        from skill.scripts.pipeline import run_pipeline

        sig = inspect.signature(run_pipeline)
        assert "render_form_8949" in sig.parameters, (
            "render_form_8949 parameter missing from run_pipeline"
        )

    def test_render_form_8949_default_is_true(self):
        """The default value should be True."""
        import inspect

        from skill.scripts.pipeline import run_pipeline

        sig = inspect.signature(run_pipeline)
        param = sig.parameters["render_form_8949"]
        assert param.default is True

    def test_render_form_8949_gate_suppresses_8949(self, tmp_path):
        """When render_form_8949=False and render_schedule_d=True, 8949 is
        suppressed but Schedule D is still rendered."""
        import json

        from skill.scripts.pipeline import run_pipeline

        # Create a minimal taxpayer with a 1099-B transaction so
        # Schedule D + Form 8949 would normally render.
        taxpayer_info = {
            "schema_version": "0.1.0",
            "tax_year": 2025,
            "filing_status": "single",
            "taxpayer": {
                "first_name": "Pat",
                "last_name": "Taxpayer",
                "ssn": "123-45-6789",
                "date_of_birth": "1985-06-15",
            },
            "address": {
                "street1": "1 Main St",
                "city": "Anywhere",
                "state": "CA",
                "zip": "90001",
            },
            "w2s": [
                {
                    "employer_name": "Acme",
                    "box1_wages": "65000.00",
                    "box2_federal_income_tax_withheld": "7500.00",
                }
            ],
            "forms_1099_b": [
                {
                    "broker_name": "Broker Inc",
                    "transactions": [
                        {
                            "description": "100 sh AAPL",
                            "proceeds": "5000.00",
                            "cost_basis": "4000.00",
                            "date_sold": "2025-06-01",
                            "date_acquired": "2024-01-15",
                            "is_long_term": True,
                            "basis_reported_to_irs": True,
                        }
                    ],
                }
            ],
        }
        info_path = tmp_path / "taxpayer_info.json"
        info_path.write_text(json.dumps(taxpayer_info))

        input_dir = tmp_path / "input"
        input_dir.mkdir()
        output_dir = tmp_path / "output"

        result = run_pipeline(
            input_dir=input_dir,
            taxpayer_info_path=info_path,
            output_dir=output_dir,
            render_form_8949=False,
            render_state_returns=False,
            build_paper_bundle=False,
            emit_ffff_map=False,
        )

        rendered_names = [p.name for p in result.rendered_paths]
        # Schedule D should be present
        assert "schedule_d.pdf" in rendered_names
        # Form 8949 should NOT be present
        assert all("8949" not in name for name in rendered_names), (
            f"form_8949 should be suppressed but found: {rendered_names}"
        )


# ===========================================================================
# Task 2: Constants migration
# ===========================================================================


class TestConstantsMigration:
    """EITC and CTC constants should load from ty2025-constants.json."""

    # -- EITC constants ---------------------------------------------------

    def test_eitc_phase_in_rate_0_children(self):
        assert C.eitc_phase_in_rate(0) == pytest.approx(0.0765)

    def test_eitc_phase_in_rate_1_child(self):
        assert C.eitc_phase_in_rate(1) == pytest.approx(0.34)

    def test_eitc_phase_in_rate_2_children(self):
        assert C.eitc_phase_in_rate(2) == pytest.approx(0.40)

    def test_eitc_phase_in_rate_3_or_more(self):
        assert C.eitc_phase_in_rate(3) == pytest.approx(0.45)
        assert C.eitc_phase_in_rate(5) == pytest.approx(0.45)

    def test_eitc_earned_income_for_max_credit(self):
        assert C.eitc_earned_income_for_max_credit(0) == 8490
        assert C.eitc_earned_income_for_max_credit(1) == 12730
        assert C.eitc_earned_income_for_max_credit(2) == 17880
        assert C.eitc_earned_income_for_max_credit(3) == 17880

    def test_eitc_phase_out_rate(self):
        assert C.eitc_phase_out_rate(0) == pytest.approx(0.0765)
        assert C.eitc_phase_out_rate(1) == pytest.approx(0.1598)
        assert C.eitc_phase_out_rate(2) == pytest.approx(0.2106)
        assert C.eitc_phase_out_rate(3) == pytest.approx(0.2106)

    def test_eitc_phase_out_begin_single(self):
        assert C.eitc_phase_out_begin(0, "single") == 10620
        assert C.eitc_phase_out_begin(1, "single") == 23350
        assert C.eitc_phase_out_begin(2, "single") == 23350
        assert C.eitc_phase_out_begin(3, "single") == 23350

    def test_eitc_phase_out_begin_mfj(self):
        assert C.eitc_phase_out_begin(0, "mfj") == 17730
        assert C.eitc_phase_out_begin(1, "mfj") == 30470
        assert C.eitc_phase_out_begin(2, "mfj") == 30470
        assert C.eitc_phase_out_begin(3, "mfj") == 30470

    # -- CTC / ODC / ACTC constants ---------------------------------------

    def test_odc_per_dependent(self):
        assert C.odc_per_dependent() == 500

    def test_actc_earned_income_floor(self):
        assert C.actc_earned_income_floor() == 2500

    def test_actc_earned_income_rate(self):
        assert C.actc_earned_income_rate() == pytest.approx(0.15)


# ===========================================================================
# Task 3: K-1 Box 14 SE earnings → Schedule SE
# ===========================================================================


class TestK1Box14SEEarnings:
    """K-1 Box 14 self-employment earnings should flow to Schedule SE."""

    def test_model_has_box14_field(self):
        """ScheduleK1 model should have box14_self_employment_earnings."""
        k1 = ScheduleK1(source_name="Test LP")
        assert hasattr(k1, "box14_self_employment_earnings")
        assert k1.box14_self_employment_earnings == Decimal("0")

    def test_k1_box14_included_in_earned_income(self):
        """earned_income() should include K-1 Box 14 SE earnings."""
        ret = _base_return(
            w2s=[
                W2(
                    employer_name="Acme",
                    box1_wages=Decimal("50000"),
                    box2_federal_income_tax_withheld=Decimal("5000"),
                )
            ],
            schedules_k1=[
                ScheduleK1(
                    source_name="Test LP",
                    box14_self_employment_earnings=Decimal("30000"),
                )
            ],
        )
        ei = earned_income(ret)
        # Earned income = W-2 $50k + K-1 Box 14 $30k = $80k
        assert ei == Decimal("80000")

    def test_k1_box14_se_tax_computed(self):
        """A return with K-1 Box 14 $30k should have SE tax in total_tax.

        Tenforty computes SE tax internally and folds it into
        federal_total_tax. The engine extracts the delta
        (total_tax - income_tax) as other_taxes_total, which contains
        SE + additional Medicare. We verify the delta is approximately
        the SE tax on $30k: net = $30k * 0.9235 = $27,705, then
        SE tax = $27,705 * 0.153 ~ $4,239.
        """
        ret = _base_return(
            schedules_k1=[
                ScheduleK1(
                    source_name="Test Partnership LP",
                    source_type="partnership",
                    box14_self_employment_earnings=Decimal("30000"),
                )
            ],
        )
        result = compute(ret)
        # other_taxes_total contains SE tax + additional Medicare (if any).
        # For $30k SE income with no W-2, the delta should be ~$4,239.
        other_taxes = result.computed.other_taxes_total or Decimal("0")
        assert other_taxes > Decimal("4000"), (
            f"other_taxes_total {other_taxes} too low for $30k K-1 SE earnings"
        )
        assert other_taxes < Decimal("4600"), (
            f"other_taxes_total {other_taxes} too high for $30k K-1 SE earnings"
        )

        # Also verify total_tax is significantly higher than income tax alone
        # (meaning SE tax was folded in).
        total = result.computed.total_tax or Decimal("0")
        tentative = result.computed.tentative_tax or Decimal("0")
        assert total > tentative, (
            "total_tax should exceed tentative_tax when SE tax is present"
        )

    def test_k1_box14_zero_no_se_tax(self):
        """A K-1 with zero Box 14 should not trigger SE tax."""
        ret = _base_return(
            schedules_k1=[
                ScheduleK1(
                    source_name="Test LP",
                    box14_self_employment_earnings=Decimal("0"),
                )
            ],
        )
        result = compute(ret)
        # With zero SE income, other_taxes_total should be zero
        # (no SE tax, no additional Medicare, no NIIT).
        other_taxes = result.computed.other_taxes_total or Decimal("0")
        assert other_taxes == Decimal("0"), (
            f"other_taxes_total should be 0 with zero SE income, got {other_taxes}"
        )

    def test_k1_box14_multiple_k1s_sum(self):
        """Multiple K-1s with Box 14 should sum their SE earnings."""
        ret = _base_return(
            schedules_k1=[
                ScheduleK1(
                    source_name="Partnership A",
                    box14_self_employment_earnings=Decimal("15000"),
                ),
                ScheduleK1(
                    source_name="Partnership B",
                    box14_self_employment_earnings=Decimal("10000"),
                ),
            ],
        )
        ei = earned_income(ret)
        # Both K-1 Box 14 amounts should be summed
        assert ei == Decimal("25000")
