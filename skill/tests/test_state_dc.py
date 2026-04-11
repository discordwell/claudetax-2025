"""Tests for the DC StatePlugin (skill/scripts/states/dc.py).

Covers:
- Runtime Protocol satisfaction.
- Meta fields per task spec (code, starting_point, has_income_tax, reciprocity).
- Universal nonresident exemption -> state_tax == 0 with a documented reason.
- Resident tax on a $65k AGI single filer (matches hand calc on the TY2025
  bracket table).
- Part-year proration is strictly between zero and the full-year resident tax.
- ReciprocityTable.load() DC partners match the plugin's meta.reciprocity_partners.
- form_ids() surfaces at least DC Form D-40.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from skill.scripts.models import (
    Address,
    CanonicalReturn,
    FilingStatus,
    Person,
    ResidencyStatus,
    W2,
)
from skill.scripts.states.dc import (
    DC_TY2025_BRACKETS,
    DC_TY2025_STANDARD_DEDUCTION,
    PLUGIN as DC_PLUGIN,
    DistrictOfColumbiaPlugin,
)
from skill.scripts.states._plugin_api import (
    FederalTotals,
    ReciprocityTable,
    StatePlugin,
    StateStartingPoint,
    SubmissionChannel,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def canonical_return() -> CanonicalReturn:
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=Person(
            first_name="Dana",
            last_name="Columbia",
            ssn="123-45-6789",
            date_of_birth=dt.date(1990, 6, 1),
        ),
        address=Address(
            street1="100 Independence Ave SE",
            city="Washington",
            state="DC",
            zip="20003",
        ),
        w2s=[W2(employer_name="Federal Agency", box1_wages=Decimal("65000"))],
    )


@pytest.fixture
def federal_65k() -> FederalTotals:
    """A typical $65k single-filer federal totals snapshot."""
    return FederalTotals(
        filing_status=FilingStatus.SINGLE,
        num_dependents=0,
        adjusted_gross_income=Decimal("65000"),
        taxable_income=Decimal("49250"),
        total_federal_tax=Decimal("5600"),
        federal_income_tax=Decimal("5600"),
        federal_standard_deduction=Decimal("15750"),
        federal_itemized_deductions_total=Decimal("0"),
        deduction_taken=Decimal("15750"),
        federal_withholding_from_w2s=Decimal("6000"),
    )


# ---------------------------------------------------------------------------
# Protocol + meta
# ---------------------------------------------------------------------------


def test_protocol_satisfied_at_runtime():
    assert isinstance(DC_PLUGIN, StatePlugin)


def test_plugin_is_frozen_dataclass_instance():
    assert isinstance(DC_PLUGIN, DistrictOfColumbiaPlugin)


def test_meta_code_is_dc():
    assert DC_PLUGIN.meta.code == "DC"


def test_meta_name_is_district_of_columbia():
    assert DC_PLUGIN.meta.name == "District of Columbia"


def test_meta_has_income_tax_true():
    assert DC_PLUGIN.meta.has_income_tax is True


def test_meta_starting_point_is_federal_agi():
    assert DC_PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI


def test_meta_submission_channel_is_free_portal():
    assert DC_PLUGIN.meta.submission_channel == SubmissionChannel.STATE_DOR_FREE_PORTAL


def test_meta_reciprocity_partners_contains_md_and_va():
    partners = DC_PLUGIN.meta.reciprocity_partners
    assert "MD" in partners
    assert "VA" in partners


def test_meta_dor_and_efile_urls():
    assert DC_PLUGIN.meta.dor_url == "https://otr.cfo.dc.gov/"
    assert DC_PLUGIN.meta.free_efile_url == "https://mytax.dc.gov/"


def test_meta_supports_ty2025():
    assert 2025 in DC_PLUGIN.meta.supported_tax_years


def test_meta_is_frozen():
    with pytest.raises(Exception):
        DC_PLUGIN.meta.code = "CA"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# compute() — nonresident universal exemption
# ---------------------------------------------------------------------------


def test_compute_nonresident_state_tax_is_zero(canonical_return, federal_65k):
    state_return = DC_PLUGIN.compute(
        canonical_return, federal_65k, ResidencyStatus.NONRESIDENT, days_in_state=0
    )
    assert state_return.state == "DC"
    assert state_return.residency == ResidencyStatus.NONRESIDENT
    assert state_return.state_specific["state_tax"] == Decimal("0")


def test_compute_nonresident_has_exemption_reason(canonical_return, federal_65k):
    state_return = DC_PLUGIN.compute(
        canonical_return, federal_65k, ResidencyStatus.NONRESIDENT, days_in_state=0
    )
    reason = state_return.state_specific["reason"]
    assert "nonresident" in reason.lower()
    assert "exemption" in reason.lower()


# ---------------------------------------------------------------------------
# compute() — resident calc
# ---------------------------------------------------------------------------


def test_compute_resident_tax_is_positive(canonical_return, federal_65k):
    state_return = DC_PLUGIN.compute(
        canonical_return, federal_65k, ResidencyStatus.RESIDENT, days_in_state=365
    )
    assert state_return.state_specific["state_tax"] > 0


def test_compute_resident_tax_65k_matches_hand_calc(canonical_return, federal_65k):
    """$65k AGI, single filer, TY2025 DC brackets.

    Taxable = 65000 - 15000 (single std ded) = 50000.
    Bracket (40k, 60k]: base 2200 + (50000 - 40000) * 0.065 = 2200 + 650 = 2850.
    """
    state_return = DC_PLUGIN.compute(
        canonical_return, federal_65k, ResidencyStatus.RESIDENT, days_in_state=365
    )
    assert state_return.state_specific["state_tax"] == Decimal("2850.00")


def test_compute_resident_taxable_income_matches_agi_minus_std(
    canonical_return, federal_65k
):
    state_return = DC_PLUGIN.compute(
        canonical_return, federal_65k, ResidencyStatus.RESIDENT, days_in_state=365
    )
    # 65000 - 15000 = 50000
    assert state_return.state_specific["taxable_income"] == Decimal("50000")


# ---------------------------------------------------------------------------
# compute() — part-year proration
# ---------------------------------------------------------------------------


def test_compute_part_year_180_days_is_strictly_between(
    canonical_return, federal_65k
):
    full_year = DC_PLUGIN.compute(
        canonical_return, federal_65k, ResidencyStatus.RESIDENT, days_in_state=365
    )
    part_year = DC_PLUGIN.compute(
        canonical_return, federal_65k, ResidencyStatus.PART_YEAR, days_in_state=180
    )
    resident_tax = full_year.state_specific["state_tax"]
    part_year_tax = part_year.state_specific["state_tax"]
    assert Decimal("0") < part_year_tax < resident_tax


def test_compute_part_year_approximately_half(canonical_return, federal_65k):
    """180/365 ~= 0.493, so part-year tax should be close to resident_tax * 0.493."""
    full_year = DC_PLUGIN.compute(
        canonical_return, federal_65k, ResidencyStatus.RESIDENT, days_in_state=365
    )
    part_year = DC_PLUGIN.compute(
        canonical_return, federal_65k, ResidencyStatus.PART_YEAR, days_in_state=180
    )
    resident_tax = full_year.state_specific["state_tax"]
    expected = (resident_tax * Decimal(180) / Decimal(365)).quantize(Decimal("0.01"))
    assert part_year.state_specific["state_tax"] == expected


# ---------------------------------------------------------------------------
# Bracket table sanity
# ---------------------------------------------------------------------------


def test_bracket_table_has_seven_brackets():
    assert len(DC_TY2025_BRACKETS) == 7


def test_standard_deduction_table_covers_all_filing_statuses():
    for fs in FilingStatus:
        assert fs in DC_TY2025_STANDARD_DEDUCTION
        assert DC_TY2025_STANDARD_DEDUCTION[fs] > Decimal("0")


# ---------------------------------------------------------------------------
# apportion_income()
# ---------------------------------------------------------------------------


def test_apportion_income_resident_all_state_source(canonical_return):
    apportionment = DC_PLUGIN.apportion_income(
        canonical_return, ResidencyStatus.RESIDENT, days_in_state=365
    )
    assert apportionment.state_source_wages == Decimal("65000")


def test_apportion_income_nonresident_all_zero(canonical_return):
    apportionment = DC_PLUGIN.apportion_income(
        canonical_return, ResidencyStatus.NONRESIDENT, days_in_state=0
    )
    assert apportionment.state_source_total == Decimal("0")


def test_apportion_income_part_year_prorated(canonical_return):
    apportionment = DC_PLUGIN.apportion_income(
        canonical_return, ResidencyStatus.PART_YEAR, days_in_state=180
    )
    expected = (Decimal("65000") * Decimal(180) / Decimal(365)).quantize(
        Decimal("0.01")
    )
    assert apportionment.state_source_wages == expected


# ---------------------------------------------------------------------------
# render_pdfs / form_ids
# ---------------------------------------------------------------------------


def test_render_pdfs_returns_empty_list(canonical_return, federal_65k, tmp_path):
    state_return = DC_PLUGIN.compute(
        canonical_return, federal_65k, ResidencyStatus.RESIDENT, days_in_state=365
    )
    assert DC_PLUGIN.render_pdfs(state_return, tmp_path) == []


def test_form_ids_includes_d_40():
    form_ids = DC_PLUGIN.form_ids()
    assert form_ids == ["DC Form D-40"]


# ---------------------------------------------------------------------------
# ReciprocityTable consistency
# ---------------------------------------------------------------------------


def test_reciprocity_table_dc_partners_match_plugin_meta():
    """Every bilateral DC partner in the loaded ReciprocityTable must also be
    present in DC_PLUGIN.meta.reciprocity_partners (the plugin is allowed to
    list additional partners, though DC's canonical pairs are just MD and VA)."""
    table = ReciprocityTable.load()
    dc_partners_from_table = table.partners_of("DC")
    assert dc_partners_from_table == frozenset({"MD", "VA"})
    # Every partner from the table must appear in plugin meta.
    plugin_partners = set(DC_PLUGIN.meta.reciprocity_partners)
    assert dc_partners_from_table.issubset(plugin_partners)
    # And the plugin meta should not claim non-existent bilateral partners.
    assert plugin_partners == set(dc_partners_from_table)


def test_reciprocity_table_dc_universal_exemption_applies():
    table = ReciprocityTable.load()
    assert table.dc_universal_exemption
    assert table.dc_exempts_nonresident_employee("DC")
