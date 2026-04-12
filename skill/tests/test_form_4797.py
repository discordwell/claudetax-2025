"""Tests for Form 4797 (Sales of Business Property).

Tests cover:
  - Model validation (Form4797Sale on CanonicalReturn)
  - Layer 1 compute: §1231 gain (Part I → Schedule D), §1245 recapture
    (Part II → ordinary), §1250 recapture (Part III → 25% rate),
    mixed scenario, losses
  - Layer 2 render: scaffold PDF is created without error
  - Engine integration: Form 4797 ordinary gains flow through Schedule 1
    line 4, §1231 gains flow to long-term capital gains
  - Schedule 1 line 4 wiring
  - FFFF entry map includes Form 4797 entries

Sources for expected values:
  - IRS Form 4797 (TY2025) and Instructions for Form 4797
  - §1231, §1245, §1250 recapture rules per IRC
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.models import (
    Address,
    CanonicalReturn,
    FilingStatus,
    Form4797Sale,
    Person,
)
from skill.scripts.output.form_4797 import (
    Form4797Fields,
    Form4797SaleResult,
    _classify_sale,
    compute_form_4797_fields,
    form_4797_required,
    render_form_4797_pdf,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _addr() -> Address:
    return Address(street1="1 Main St", city="Anywhere", state="CA", zip="90001")


def _person() -> Person:
    return Person(
        first_name="Pat",
        last_name="Taxpayer",
        ssn="123-45-6789",
        date_of_birth=dt.date(1985, 6, 15),
    )


def _return(
    sales: list[Form4797Sale] | None = None,
    status: FilingStatus = FilingStatus.SINGLE,
) -> CanonicalReturn:
    return CanonicalReturn(
        tax_year=2025,
        filing_status=status,
        taxpayer=_person(),
        address=_addr(),
        forms_4797=sales or [],
    )


def _sale_1231(
    *,
    desc: str = "Timber Land",
    gross: str = "50000",
    basis: str = "30000",
    depr: str = "0",
) -> Form4797Sale:
    """Create a §1231 sale (property held > 1 year, no recapture)."""
    return Form4797Sale(
        description=desc,
        date_acquired=dt.date(2015, 1, 1),
        date_sold=dt.date(2025, 6, 15),
        gross_sales_price=Decimal(gross),
        cost_or_basis=Decimal(basis),
        depreciation_allowed=Decimal(depr),
        section_type="1231",
    )


def _sale_1245(
    *,
    desc: str = "Office Equipment",
    gross: str = "10000",
    basis: str = "15000",
    depr: str = "8000",
) -> Form4797Sale:
    """Create a §1245 sale (tangible personal property with depreciation)."""
    return Form4797Sale(
        description=desc,
        date_acquired=dt.date(2020, 3, 1),
        date_sold=dt.date(2025, 9, 30),
        gross_sales_price=Decimal(gross),
        cost_or_basis=Decimal(basis),
        depreciation_allowed=Decimal(depr),
        section_type="1245",
    )


def _sale_1250(
    *,
    desc: str = "Rental Building",
    gross: str = "300000",
    basis: str = "250000",
    depr: str = "40000",
) -> Form4797Sale:
    """Create a §1250 sale (real property with depreciation)."""
    return Form4797Sale(
        description=desc,
        date_acquired=dt.date(2010, 7, 1),
        date_sold=dt.date(2025, 12, 1),
        gross_sales_price=Decimal(gross),
        cost_or_basis=Decimal(basis),
        depreciation_allowed=Decimal(depr),
        section_type="1250",
    )


# ---------------------------------------------------------------------------
# Model validation
# ---------------------------------------------------------------------------


class TestModel:
    def test_form4797sale_on_canonical_return(self):
        """Form4797Sale can be placed on CanonicalReturn.forms_4797."""
        sale = _sale_1231()
        ret = _return(sales=[sale])
        assert len(ret.forms_4797) == 1
        assert ret.forms_4797[0].section_type == "1231"

    def test_empty_forms_4797_is_default(self):
        ret = _return()
        assert ret.forms_4797 == []

    def test_section_type_validation(self):
        """section_type must be 1231, 1245, or 1250."""
        with pytest.raises(Exception):
            Form4797Sale(
                description="Bad",
                date_sold=dt.date(2025, 6, 1),
                gross_sales_price=Decimal("100"),
                cost_or_basis=Decimal("50"),
                section_type="9999",  # type: ignore
            )


# ---------------------------------------------------------------------------
# classify_sale — unit tests for the classification engine
# ---------------------------------------------------------------------------


class TestClassifySale:
    def test_1231_gain(self):
        """Pure §1231 sale with gain → all gain is §1231."""
        sale = _sale_1231(gross="50000", basis="30000", depr="0")
        result = _classify_sale(sale)
        assert result.total_gain_or_loss == Decimal("20000")
        assert result.ordinary_gain == Decimal("0")
        assert result.section_1231_gain_or_loss == Decimal("20000")
        assert result.unrecaptured_1250_gain == Decimal("0")

    def test_1231_loss(self):
        """Pure §1231 sale with loss → all loss is §1231."""
        sale = _sale_1231(gross="20000", basis="30000", depr="0")
        result = _classify_sale(sale)
        assert result.total_gain_or_loss == Decimal("-10000")
        assert result.section_1231_gain_or_loss == Decimal("-10000")

    def test_1245_full_recapture(self):
        """§1245 sale where gain <= depreciation → all gain is ordinary."""
        # Basis 15000, depr 8000 → adj basis 7000, sell for 10000 → gain 3000
        # Gain (3000) < depr (8000) → all 3000 is ordinary
        sale = _sale_1245(gross="10000", basis="15000", depr="8000")
        result = _classify_sale(sale)
        assert result.total_gain_or_loss == Decimal("3000")
        assert result.ordinary_gain == Decimal("3000")
        assert result.section_1231_gain_or_loss == Decimal("0")
        assert result.unrecaptured_1250_gain == Decimal("0")

    def test_1245_gain_exceeds_depreciation(self):
        """§1245 sale where gain > depreciation → depreciation is ordinary,
        excess is §1231 gain."""
        # Basis 15000, depr 8000 → adj basis 7000, sell for 20000 → gain 13000
        # Ordinary = min(13000, 8000) = 8000, excess = 5000 → §1231 gain
        sale = _sale_1245(gross="20000", basis="15000", depr="8000")
        result = _classify_sale(sale)
        assert result.total_gain_or_loss == Decimal("13000")
        assert result.ordinary_gain == Decimal("8000")
        assert result.section_1231_gain_or_loss == Decimal("5000")

    def test_1245_loss(self):
        """§1245 sale with a loss → entire loss is §1231 (no recapture)."""
        # Basis 15000, depr 8000 → adj basis 7000, sell for 5000 → loss -2000
        sale = _sale_1245(gross="5000", basis="15000", depr="8000")
        result = _classify_sale(sale)
        assert result.total_gain_or_loss == Decimal("-2000")
        assert result.ordinary_gain == Decimal("0")
        assert result.section_1231_gain_or_loss == Decimal("-2000")

    def test_1250_gain_within_depreciation(self):
        """§1250 sale where gain <= depreciation → all gain is
        unrecaptured §1250 (25% rate), no §1231 excess."""
        # Basis 250000, depr 40000 → adj basis 210000, sell for 230000 → gain 20000
        # Gain (20000) < depr (40000) → unrecaptured = 20000, §1231 = 0
        sale = _sale_1250(gross="230000", basis="250000", depr="40000")
        result = _classify_sale(sale)
        assert result.total_gain_or_loss == Decimal("20000")
        assert result.ordinary_gain == Decimal("0")  # no additional depreciation
        assert result.unrecaptured_1250_gain == Decimal("20000")
        assert result.section_1231_gain_or_loss == Decimal("0")

    def test_1250_gain_exceeds_depreciation(self):
        """§1250 sale where gain > depreciation → unrecaptured = depreciation,
        excess is §1231 gain."""
        # Basis 250000, depr 40000 → adj basis 210000, sell for 300000 → gain 90000
        # Unrecaptured = min(90000, 40000) = 40000, excess = 50000 → §1231
        sale = _sale_1250(gross="300000", basis="250000", depr="40000")
        result = _classify_sale(sale)
        assert result.total_gain_or_loss == Decimal("90000")
        assert result.ordinary_gain == Decimal("0")
        assert result.unrecaptured_1250_gain == Decimal("40000")
        assert result.section_1231_gain_or_loss == Decimal("50000")

    def test_1250_loss(self):
        """§1250 sale with a loss → entire loss is §1231, no recapture."""
        sale = _sale_1250(gross="190000", basis="250000", depr="40000")
        result = _classify_sale(sale)
        # adj basis = 210000, sell for 190000 → loss -20000
        assert result.total_gain_or_loss == Decimal("-20000")
        assert result.ordinary_gain == Decimal("0")
        assert result.unrecaptured_1250_gain == Decimal("0")
        assert result.section_1231_gain_or_loss == Decimal("-20000")


# ---------------------------------------------------------------------------
# compute_form_4797_fields — integration tests
# ---------------------------------------------------------------------------


class TestComputeForm4797Fields:
    def test_empty_return(self):
        """No Form 4797 sales → empty fields."""
        ret = _return(sales=[])
        fields = compute_form_4797_fields(ret)
        assert fields.part_i_net_gain_or_loss == Decimal("0")
        assert fields.part_ii_ordinary_gain_or_loss == Decimal("0")
        assert fields.schedule_1_line_4 == Decimal("0")
        assert fields.schedule_d_line_11 == Decimal("0")

    def test_pure_1231_gain_flows_to_schedule_d(self):
        """§1231 net gain → Schedule D line 11 (long-term capital gain),
        Schedule 1 line 4 stays at $0."""
        sale = _sale_1231(gross="50000", basis="30000")
        ret = _return(sales=[sale])
        fields = compute_form_4797_fields(ret)
        assert fields.part_i_net_gain_or_loss == Decimal("20000")
        assert fields.schedule_d_line_11 == Decimal("20000")
        assert fields.schedule_1_line_4 == Decimal("0")

    def test_pure_1231_loss_flows_to_schedule_1(self):
        """§1231 net loss → Schedule 1 line 4 (ordinary loss),
        Schedule D line 11 stays at $0."""
        sale = _sale_1231(gross="20000", basis="30000")
        ret = _return(sales=[sale])
        fields = compute_form_4797_fields(ret)
        assert fields.part_i_net_gain_or_loss == Decimal("-10000")
        assert fields.schedule_1_line_4 == Decimal("-10000")
        assert fields.schedule_d_line_11 == Decimal("0")

    def test_1245_ordinary_gain_flows_to_schedule_1(self):
        """§1245 ordinary recapture → Schedule 1 line 4."""
        sale = _sale_1245(gross="10000", basis="15000", depr="8000")
        ret = _return(sales=[sale])
        fields = compute_form_4797_fields(ret)
        # gain = 10000 - (15000 - 8000) = 3000, all ordinary
        assert fields.part_ii_ordinary_gain_or_loss == Decimal("3000")
        assert fields.schedule_1_line_4 == Decimal("3000")

    def test_1250_unrecaptured_flows_to_schedule_d_line_19(self):
        """§1250 unrecaptured gain → Schedule D line 19 (25% rate)."""
        sale = _sale_1250(gross="300000", basis="250000", depr="40000")
        ret = _return(sales=[sale])
        fields = compute_form_4797_fields(ret)
        assert fields.part_iii_total_unrecaptured_1250_gain == Decimal("40000")
        assert fields.schedule_d_line_19 == Decimal("40000")

    def test_mixed_scenario(self):
        """Mixed sale types: §1231 gain, §1245 ordinary, §1250 unrecaptured.

        §1231 sale: gross 50000, basis 30000 → gain 20000 (§1231)
        §1245 sale: gross 10000, basis 15000, depr 8000 → gain 3000 (ordinary)
        §1250 sale: gross 300000, basis 250000, depr 40000 → gain 90000
          (40000 unrecaptured, 50000 §1231)

        Part I §1231 net = 20000 (from 1231) + 50000 (from 1250 excess) = 70000
        Part II ordinary = 3000
        Part III unrecaptured = 40000

        Schedule 1 line 4 = 3000 (ordinary only; §1231 net is positive → Schedule D)
        Schedule D line 11 = 70000
        Schedule D line 19 = 40000
        """
        sales = [
            _sale_1231(gross="50000", basis="30000"),
            _sale_1245(gross="10000", basis="15000", depr="8000"),
            _sale_1250(gross="300000", basis="250000", depr="40000"),
        ]
        ret = _return(sales=sales)
        fields = compute_form_4797_fields(ret)

        assert fields.part_i_net_gain_or_loss == Decimal("70000")
        assert fields.part_ii_ordinary_gain_or_loss == Decimal("3000")
        assert fields.part_iii_total_unrecaptured_1250_gain == Decimal("40000")
        assert fields.schedule_1_line_4 == Decimal("3000")
        assert fields.schedule_d_line_11 == Decimal("70000")
        assert fields.schedule_d_line_19 == Decimal("40000")

    def test_mixed_net_1231_loss(self):
        """When §1231 gains and losses net to a loss, the loss goes to
        Schedule 1 line 4 (ordinary) instead of Schedule D."""
        sales = [
            _sale_1231(gross="20000", basis="50000"),  # loss -30000
            _sale_1231(gross="45000", basis="30000"),   # gain +15000
        ]
        ret = _return(sales=sales)
        fields = compute_form_4797_fields(ret)
        # Net §1231 = -30000 + 15000 = -15000 (loss)
        assert fields.part_i_net_gain_or_loss == Decimal("-15000")
        assert fields.schedule_1_line_4 == Decimal("-15000")
        assert fields.schedule_d_line_11 == Decimal("0")


# ---------------------------------------------------------------------------
# form_4797_required
# ---------------------------------------------------------------------------


class TestForm4797Required:
    def test_required_when_sales_present(self):
        ret = _return(sales=[_sale_1231()])
        assert form_4797_required(ret) is True

    def test_not_required_when_no_sales(self):
        ret = _return(sales=[])
        assert form_4797_required(ret) is False


# ---------------------------------------------------------------------------
# Layer 2: render test
# ---------------------------------------------------------------------------


class TestRenderForm4797:
    def test_render_creates_pdf(self, tmp_path: Path):
        """render_form_4797_pdf produces a nonempty PDF file."""
        sale = _sale_1245(gross="10000", basis="15000", depr="8000")
        ret = _return(sales=[sale])
        fields = compute_form_4797_fields(ret)
        out_path = tmp_path / "form_4797.pdf"
        result_path = render_form_4797_pdf(fields, out_path)
        assert result_path.exists()
        assert result_path.stat().st_size > 0

    def test_render_mixed_creates_pdf(self, tmp_path: Path):
        """Render with all three section types."""
        sales = [
            _sale_1231(gross="50000", basis="30000"),
            _sale_1245(gross="10000", basis="15000", depr="8000"),
            _sale_1250(gross="300000", basis="250000", depr="40000"),
        ]
        ret = _return(sales=sales)
        fields = compute_form_4797_fields(ret)
        out_path = tmp_path / "form_4797.pdf"
        result_path = render_form_4797_pdf(fields, out_path)
        assert result_path.exists()
        assert result_path.stat().st_size > 0


# ---------------------------------------------------------------------------
# Engine integration — Form 4797 flows into engine.compute()
# ---------------------------------------------------------------------------


class TestEngineIntegration:
    def test_1245_ordinary_gain_hits_agi(self):
        """§1245 ordinary gain should flow through Schedule 1 into AGI."""
        from skill.scripts.calc.engine import compute

        sale = _sale_1245(gross="10000", basis="15000", depr="8000")
        ret = _return(sales=[sale])
        computed = compute(ret)
        # The return has no other income, so the only income is the
        # §1245 ordinary gain ($3,000). AGI should reflect this.
        agi = computed.computed.adjusted_gross_income
        assert agi is not None
        assert agi == pytest.approx(Decimal("3000"), abs=Decimal("1"))

    def test_1231_gain_hits_ltcg(self):
        """§1231 net gain should flow as long-term capital gain into AGI."""
        from skill.scripts.calc.engine import compute

        sale = _sale_1231(gross="50000", basis="30000")
        ret = _return(sales=[sale])
        computed = compute(ret)
        agi = computed.computed.adjusted_gross_income
        assert agi is not None
        # The §1231 gain of $20,000 flows as LTCG
        assert agi == pytest.approx(Decimal("20000"), abs=Decimal("1"))

    def test_1231_loss_reduces_agi(self):
        """§1231 net loss should reduce AGI via Schedule 1."""
        from skill.scripts.calc.engine import compute

        sale = _sale_1231(gross="20000", basis="30000")
        ret = _return(sales=[sale])
        computed = compute(ret)
        agi = computed.computed.adjusted_gross_income
        assert agi is not None
        # The §1231 loss of -$10,000 flows through Schedule 1
        assert agi == pytest.approx(Decimal("-10000"), abs=Decimal("1"))


# ---------------------------------------------------------------------------
# Schedule 1 line 4 wiring
# ---------------------------------------------------------------------------


class TestSchedule1Line4:
    def test_schedule_1_line_4_populated(self):
        """Schedule 1 compute should populate line 4 from Form 4797."""
        from skill.scripts.output.schedule_1 import (
            compute_schedule_1_fields,
            schedule_1_required,
        )

        sale = _sale_1245(gross="10000", basis="15000", depr="8000")
        ret = _return(sales=[sale])
        assert schedule_1_required(ret) is True

        fields = compute_schedule_1_fields(ret)
        assert fields.line_4_other_gains == Decimal("3000")

    def test_schedule_1_required_with_4797(self):
        """Schedule 1 should be required when forms_4797 is non-empty."""
        from skill.scripts.output.schedule_1 import schedule_1_required

        ret = _return(sales=[_sale_1231()])
        assert schedule_1_required(ret) is True

    def test_schedule_1_not_required_without_4797(self):
        """Schedule 1 should not be required when no income triggers exist."""
        from skill.scripts.output.schedule_1 import schedule_1_required

        ret = _return(sales=[])
        assert schedule_1_required(ret) is False


# ---------------------------------------------------------------------------
# FFFF entry map includes Form 4797
# ---------------------------------------------------------------------------


class TestFFFFEntryMap:
    def test_ffff_includes_form_4797_entries(self):
        """FFFF entry map should include Form 4797 entries when sales exist."""
        from skill.scripts.calc.engine import compute
        from skill.scripts.output.ffff_entry_map import build_ffff_entry_map

        sale = _sale_1245(gross="10000", basis="15000", depr="8000")
        ret = _return(sales=[sale])
        ret = compute(ret)
        entry_map = build_ffff_entry_map(ret)

        form_4797_entries = [e for e in entry_map.entries if e.form == "4797"]
        assert len(form_4797_entries) > 0

    def test_ffff_excludes_form_4797_when_no_sales(self):
        """FFFF entry map should NOT include Form 4797 when no sales."""
        from skill.scripts.calc.engine import compute
        from skill.scripts.output.ffff_entry_map import build_ffff_entry_map

        ret = _return(sales=[])
        ret = compute(ret)
        entry_map = build_ffff_entry_map(ret)

        form_4797_entries = [e for e in entry_map.entries if e.form == "4797"]
        assert len(form_4797_entries) == 0
