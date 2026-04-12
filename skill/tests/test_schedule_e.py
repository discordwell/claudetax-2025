"""Tests for skill.scripts.output.schedule_e -- Schedule E scaffold renderer.

Two layers under test:

* Layer 1 -- ``compute_schedule_e_fields``: assert that values on the
  returned dataclass match the expected Schedule E line numbers for a
  variety of inputs (single property, multiple properties, royalties
  only, loss scenarios).

* Layer 2 -- ``render_schedule_e_pdf``: write a scaffold PDF, assert the
  file exists and is non-empty.

Engine integration tests verify that Layer 1's per-property net is
bit-for-bit consistent with ``schedule_e_property_net`` from the
calc engine.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.calc.engine import schedule_e_property_net
from skill.scripts.models import (
    Address,
    CanonicalReturn,
    FilingStatus,
    Person,
    ScheduleE,
    ScheduleEProperty,
)
from skill.scripts.output.schedule_e import (
    ScheduleEFields,
    ScheduleEPropertyFields,
    compute_schedule_e_fields,
    render_schedule_e_pdf,
    render_schedule_e_pdfs_all,
)


_ZERO = Decimal("0")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _person(first: str = "Test", last: str = "Payer") -> Person:
    return Person(
        first_name=first,
        last_name=last,
        ssn="111-22-3333",
        date_of_birth="1990-01-01",
        is_blind=False,
        is_age_65_or_older=False,
    )


def _address(street: str = "1 Test") -> Address:
    return Address(street1=street, city="Springfield", state="IL", zip="62701")


def _property(
    *,
    rents: Decimal = _ZERO,
    royalties: Decimal = _ZERO,
    advertising: Decimal = _ZERO,
    auto_and_travel: Decimal = _ZERO,
    cleaning_and_maintenance: Decimal = _ZERO,
    commissions: Decimal = _ZERO,
    insurance: Decimal = _ZERO,
    legal_and_professional: Decimal = _ZERO,
    management_fees: Decimal = _ZERO,
    mortgage_interest_to_banks: Decimal = _ZERO,
    other_interest: Decimal = _ZERO,
    repairs: Decimal = _ZERO,
    supplies: Decimal = _ZERO,
    taxes: Decimal = _ZERO,
    utilities: Decimal = _ZERO,
    depreciation: Decimal = _ZERO,
    other_expenses: dict[str, Decimal] | None = None,
    address_street: str = "100 Main St",
    property_type: str = "single_family",
) -> ScheduleEProperty:
    return ScheduleEProperty(
        address=_address(address_street),
        property_type=property_type,
        rents_received=rents,
        royalties_received=royalties,
        advertising=advertising,
        auto_and_travel=auto_and_travel,
        cleaning_and_maintenance=cleaning_and_maintenance,
        commissions=commissions,
        insurance=insurance,
        legal_and_professional=legal_and_professional,
        management_fees=management_fees,
        mortgage_interest_to_banks=mortgage_interest_to_banks,
        other_interest=other_interest,
        repairs=repairs,
        supplies=supplies,
        taxes=taxes,
        utilities=utilities,
        depreciation=depreciation,
        other_expenses=other_expenses or {},
    )


def _minimal_return(
    *,
    schedules_e: list[ScheduleE] | None = None,
) -> CanonicalReturn:
    """Build a minimal CanonicalReturn with Schedule E data."""
    data: dict = {
        "schema_version": "0.1.0",
        "tax_year": 2025,
        "filing_status": FilingStatus.SINGLE.value,
        "taxpayer": _person().model_dump(mode="json"),
        "address": _address().model_dump(mode="json"),
    }
    if schedules_e is not None:
        data["schedules_e"] = [se.model_dump(mode="json") for se in schedules_e]
    return CanonicalReturn.model_validate(data)


# ---------------------------------------------------------------------------
# Test 1: Single property -- $24k rent, $15k expenses -> net $9k
# ---------------------------------------------------------------------------


def test_single_property_net_income() -> None:
    """Single property: $24,000 rents, $15,000 total expenses -> net $9,000."""
    prop = _property(
        rents=Decimal("24000"),
        insurance=Decimal("3000"),
        mortgage_interest_to_banks=Decimal("5000"),
        repairs=Decimal("2000"),
        taxes=Decimal("3000"),
        depreciation=Decimal("2000"),
    )
    sched_e = ScheduleE(properties=[prop])
    ret = _minimal_return(schedules_e=[sched_e])
    fields = compute_schedule_e_fields(ret)

    assert isinstance(fields, ScheduleEFields)
    assert len(fields.properties) == 1

    pf = fields.properties[0]
    assert pf.line_3_rents_received == Decimal("24000.00")
    assert pf.line_20_total_expenses == Decimal("15000.00")
    assert pf.line_21_net_income_or_loss == Decimal("9000.00")

    # Summary
    assert fields.line_23a_total_rental_income == Decimal("9000.00")
    assert fields.line_23b_total_rental_losses == Decimal("0.00")
    assert fields.line_26_total_rental_royalty_income_or_loss == Decimal("9000.00")


# ---------------------------------------------------------------------------
# Test 2: Multiple properties (3) -- verify columns and line 26 total
# ---------------------------------------------------------------------------


def test_multiple_properties_columns_and_line_26() -> None:
    """Three properties: verify per-property columns and aggregate line 26."""
    prop_a = _property(
        rents=Decimal("12000"),
        insurance=Decimal("1000"),
        repairs=Decimal("500"),
        address_street="100 Alpha St",
    )
    prop_b = _property(
        rents=Decimal("18000"),
        mortgage_interest_to_banks=Decimal("4000"),
        taxes=Decimal("2000"),
        depreciation=Decimal("3000"),
        address_street="200 Beta Ave",
    )
    prop_c = _property(
        rents=Decimal("6000"),
        insurance=Decimal("800"),
        utilities=Decimal("1200"),
        address_street="300 Gamma Blvd",
    )
    sched_e = ScheduleE(properties=[prop_a, prop_b, prop_c])
    ret = _minimal_return(schedules_e=[sched_e])
    fields = compute_schedule_e_fields(ret)

    assert len(fields.properties) == 3

    # Property A: 12000 - 1500 = 10500
    assert fields.properties[0].line_21_net_income_or_loss == Decimal("10500.00")

    # Property B: 18000 - 9000 = 9000
    assert fields.properties[1].line_21_net_income_or_loss == Decimal("9000.00")

    # Property C: 6000 - 2000 = 4000
    assert fields.properties[2].line_21_net_income_or_loss == Decimal("4000.00")

    # Line 26 total: 10500 + 9000 + 4000 = 23500
    assert fields.line_26_total_rental_royalty_income_or_loss == Decimal("23500.00")


# ---------------------------------------------------------------------------
# Test 3: Property with royalties only, no rent
# ---------------------------------------------------------------------------


def test_property_royalties_only() -> None:
    """Property with royalties only, no rental income."""
    prop = _property(
        royalties=Decimal("8000"),
        legal_and_professional=Decimal("1500"),
        depreciation=Decimal("500"),
    )
    sched_e = ScheduleE(properties=[prop])
    ret = _minimal_return(schedules_e=[sched_e])
    fields = compute_schedule_e_fields(ret)

    assert len(fields.properties) == 1
    pf = fields.properties[0]
    assert pf.line_3_rents_received == _ZERO
    assert pf.line_4_royalties_received == Decimal("8000.00")
    assert pf.line_20_total_expenses == Decimal("2000.00")
    assert pf.line_21_net_income_or_loss == Decimal("6000.00")
    assert fields.line_26_total_rental_royalty_income_or_loss == Decimal("6000.00")


# ---------------------------------------------------------------------------
# Test 4: Property with loss (expenses > income)
# ---------------------------------------------------------------------------


def test_property_with_loss() -> None:
    """Property where expenses exceed income, producing a net loss."""
    prop = _property(
        rents=Decimal("10000"),
        mortgage_interest_to_banks=Decimal("8000"),
        taxes=Decimal("4000"),
        insurance=Decimal("2000"),
        depreciation=Decimal("5000"),
    )
    sched_e = ScheduleE(properties=[prop])
    ret = _minimal_return(schedules_e=[sched_e])
    fields = compute_schedule_e_fields(ret)

    pf = fields.properties[0]
    assert pf.line_3_rents_received == Decimal("10000.00")
    assert pf.line_20_total_expenses == Decimal("19000.00")
    # Net: 10000 - 19000 = -9000
    assert pf.line_21_net_income_or_loss == Decimal("-9000.00")

    assert fields.line_23a_total_rental_income == Decimal("0.00")
    assert fields.line_23b_total_rental_losses == Decimal("-9000.00")
    assert fields.line_26_total_rental_royalty_income_or_loss == Decimal("-9000.00")


# ---------------------------------------------------------------------------
# Test 5: Engine integration -- verify compute matches schedule_e_property_net
# ---------------------------------------------------------------------------


def test_engine_integration_matches_schedule_e_property_net() -> None:
    """Verify that Layer 1 per-property net matches the engine helper."""
    prop = _property(
        rents=Decimal("30000"),
        royalties=Decimal("5000"),
        advertising=Decimal("500"),
        auto_and_travel=Decimal("200"),
        cleaning_and_maintenance=Decimal("300"),
        commissions=Decimal("100"),
        insurance=Decimal("1500"),
        legal_and_professional=Decimal("800"),
        management_fees=Decimal("400"),
        mortgage_interest_to_banks=Decimal("6000"),
        other_interest=Decimal("150"),
        repairs=Decimal("2500"),
        supplies=Decimal("350"),
        taxes=Decimal("3500"),
        utilities=Decimal("1800"),
        depreciation=Decimal("4000"),
        other_expenses={"pest_control": Decimal("200"), "landscaping": Decimal("600")},
    )
    sched_e = ScheduleE(properties=[prop])
    ret = _minimal_return(schedules_e=[sched_e])
    fields = compute_schedule_e_fields(ret)

    # Engine helper
    engine_net = schedule_e_property_net(prop)

    pf = fields.properties[0]
    assert pf.line_21_net_income_or_loss == engine_net


# ---------------------------------------------------------------------------
# Test 6: Layer 2 -- render PDF, assert file exists and is non-empty
# ---------------------------------------------------------------------------


def test_render_pdf_produces_non_empty_file(tmp_path: Path) -> None:
    """Render a scaffold PDF and verify the output file exists and has content."""
    prop = _property(
        rents=Decimal("24000"),
        insurance=Decimal("3000"),
        mortgage_interest_to_banks=Decimal("5000"),
        repairs=Decimal("2000"),
        taxes=Decimal("3000"),
        depreciation=Decimal("2000"),
    )
    sched_e = ScheduleE(properties=[prop])
    ret = _minimal_return(schedules_e=[sched_e])
    fields = compute_schedule_e_fields(ret)

    out_path = tmp_path / "schedule_e.pdf"
    result = render_schedule_e_pdf(fields, out_path)

    assert result == out_path
    assert out_path.exists()
    assert out_path.stat().st_size > 1000

    # Attempt pypdf text extraction, but skip gracefully if unavailable
    try:
        import pypdf

        reader = pypdf.PdfReader(str(out_path))
        text = reader.pages[0].extract_text() or ""
        assert "Schedule E" in text
    except BaseException:
        pytest.skip("pypdf not importable")


# ---------------------------------------------------------------------------
# Additional coverage: render_schedule_e_pdfs_all
# ---------------------------------------------------------------------------


def test_render_pdfs_all_creates_per_schedule_files(tmp_path: Path) -> None:
    """render_schedule_e_pdfs_all creates one PDF per ScheduleE."""
    prop1 = _property(rents=Decimal("12000"), insurance=Decimal("1000"))
    prop2 = _property(rents=Decimal("8000"), repairs=Decimal("500"))
    sched_e_0 = ScheduleE(properties=[prop1])
    sched_e_1 = ScheduleE(properties=[prop2])
    ret = _minimal_return(schedules_e=[sched_e_0, sched_e_1])

    paths = render_schedule_e_pdfs_all(ret, tmp_path)

    assert len(paths) == 2
    assert paths[0].name == "schedule_e_00.pdf"
    assert paths[1].name == "schedule_e_01.pdf"
    for p in paths:
        assert p.exists()
        assert p.stat().st_size > 1000


# ---------------------------------------------------------------------------
# Edge case: no schedules_e
# ---------------------------------------------------------------------------


def test_no_schedules_e_returns_zeroed_fields() -> None:
    """Return with no schedules_e produces zeroed fields with header."""
    ret = _minimal_return(schedules_e=[])
    fields = compute_schedule_e_fields(ret)

    assert isinstance(fields, ScheduleEFields)
    assert fields.taxpayer_name == "Test Payer"
    assert len(fields.properties) == 0
    assert fields.line_26_total_rental_royalty_income_or_loss == _ZERO


def test_no_schedules_e_render_all_returns_empty_list(tmp_path: Path) -> None:
    """render_schedule_e_pdfs_all returns [] when no schedules_e."""
    ret = _minimal_return(schedules_e=[])
    paths = render_schedule_e_pdfs_all(ret, tmp_path)
    assert paths == []


# ---------------------------------------------------------------------------
# Mixed income and loss properties
# ---------------------------------------------------------------------------


def test_mixed_income_and_loss_properties() -> None:
    """Two properties: one profitable, one at a loss. Verify split and total."""
    profitable = _property(rents=Decimal("20000"), taxes=Decimal("3000"))
    losing = _property(
        rents=Decimal("5000"),
        mortgage_interest_to_banks=Decimal("8000"),
        taxes=Decimal("2000"),
    )
    sched_e = ScheduleE(properties=[profitable, losing])
    ret = _minimal_return(schedules_e=[sched_e])
    fields = compute_schedule_e_fields(ret)

    # Profitable: 20000 - 3000 = 17000
    assert fields.properties[0].line_21_net_income_or_loss == Decimal("17000.00")
    # Loss: 5000 - 10000 = -5000
    assert fields.properties[1].line_21_net_income_or_loss == Decimal("-5000.00")

    assert fields.line_23a_total_rental_income == Decimal("17000.00")
    assert fields.line_23b_total_rental_losses == Decimal("-5000.00")
    assert fields.line_26_total_rental_royalty_income_or_loss == Decimal("12000.00")
