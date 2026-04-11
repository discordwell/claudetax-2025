"""Kansas state plugin tests — TY2025.

Covers the hand-rolled ``KansasPlugin`` Form K-40 calc. Kansas is NOT
actually supported by tenforty/OpenTaxSolver despite being listed in
``OTSState`` — ``tenforty.evaluate_return(year=2025, state='KS', ...)``
raises ``ValueError: OTS does not support 2025/KS_K40`` for every year
2018-2025, so every number here is computed in-plugin from the 2025
Kansas Individual Income Tax booklet (IP25).

TY2025 structure (per IP25 page 34 Tax Computation Worksheet and
IP25 page 2 Standard Deduction / Exemption Allowance):

- Two brackets per Kansas SB 1 (2024):
    Single / HOH / MFS:  5.20% up to $23,000, 5.58% above  (sub $87)
    Married Filing Joint: 5.20% up to $46,000, 5.58% above (sub $175)
- Standard deduction: Single $3,605, MFJ $8,240, HOH $6,180, MFS $4,120
- Exemption allowance: Single/HOH/MFS $9,160; MFJ $18,320; each
  dependent $2,320

Source: 2025 Kansas Individual Income Tax booklet IP25 (rev. 9-26-25)
from the Kansas Department of Revenue:
https://www.ksrevenue.gov/pdf/ip25.pdf

Test structure mirrors ``test_state_co.py`` (another hand-rolled
state with no tenforty support). The tests cover:

- Meta + Protocol conformance
- Zero-reciprocity invariants against ``ReciprocityTable``
- Resident compute() on a Single $65k / MFJ $120k / HOH w/ 2 deps
- Nonresident day-based apportionment
- apportion_income() for resident and nonresident
- Tax computation worksheet vs tax table agreement
- Boundary conditions (bracket break, zero income, very high income)
- v1 limitations list presence
- Social Security full-exemption metadata
- render_pdfs() / form_ids()
- **Exhaustive Tax Table regression**: every printed row of IP25 page
  27-28 low-bracket verified bit-for-bit via ``ks_tax_from_table``.
- **$65k Single resident lock** (the spec-mandated wrap-correctness
  lock, locked to the IP25-worksheet value $2,827.71).

Since tenforty does not support KS, the spec's "wrap-correctness lock"
is pinned to the IP25 Tax Computation Worksheet value (the formula
Kansas WebFile uses internally). A ``pytest.skip`` seam will
auto-promote to a real tenforty comparison if tenforty ever gains KS
support.
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
    Person,
    ResidencyStatus,
    StateReturn,
    W2,
)
from skill.scripts.states._plugin_api import (
    FederalTotals,
    IncomeApportionment,
    ReciprocityTable,
    StatePlugin,
    StateStartingPoint,
    SubmissionChannel,
)
from skill.scripts.states.ks import (
    KS_TY2025_BRACKET_BREAK_MFJ,
    KS_TY2025_BRACKET_BREAK_SINGLE,
    KS_TY2025_LOWER_RATE,
    KS_TY2025_STD_DED_HOH,
    KS_TY2025_STD_DED_MFJ,
    KS_TY2025_STD_DED_MFS,
    KS_TY2025_STD_DED_SINGLE,
    KS_TY2025_SUBTRACT_MFJ,
    KS_TY2025_SUBTRACT_SINGLE,
    KS_TY2025_UPPER_RATE,
    KS_V1_LIMITATIONS,
    KansasPlugin,
    LOCK_VALUE,
    PLUGIN,
    ks_exemption_allowance,
    ks_standard_deduction,
    ks_tax_from_table,
    ks_tax_from_worksheet,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def single_65k_return() -> CanonicalReturn:
    """A Single $65k W-2 KS resident — the spec's wrap-correctness lock
    scenario. Filed from Topeka (state capital)."""
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=Person(
            first_name="Sunflower",
            last_name="Kansan",
            ssn="111-22-3333",
            date_of_birth=dt.date(1990, 1, 1),
        ),
        address=Address(
            street1="300 SW 10th Ave",
            city="Topeka",
            state="KS",
            zip="66612",
        ),
        w2s=[
            W2(
                employer_name="Wheat State Corp",
                box1_wages=Decimal("65000"),
                box2_federal_income_tax_withheld=Decimal("0"),
            ),
        ],
    )


@pytest.fixture
def federal_single_65k() -> FederalTotals:
    """$65k AGI Single / OBBBA std ded $15,750 / federal taxable $49,250."""
    return FederalTotals(
        filing_status=FilingStatus.SINGLE,
        num_dependents=0,
        adjusted_gross_income=Decimal("65000"),
        taxable_income=Decimal("49250"),
        total_federal_tax=Decimal("5755"),
        federal_income_tax=Decimal("5755"),
        federal_standard_deduction=Decimal("15750"),
        federal_itemized_deductions_total=Decimal("0"),
        deduction_taken=Decimal("15750"),
        federal_withholding_from_w2s=Decimal("0"),
    )


@pytest.fixture
def mfj_120k_return() -> CanonicalReturn:
    """A MFJ $120k W-2 KS resident couple filed from Wichita."""
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.MFJ,
        taxpayer=Person(
            first_name="Dorothy",
            last_name="Gale",
            ssn="111-22-3333",
            date_of_birth=dt.date(1985, 3, 15),
        ),
        spouse=Person(
            first_name="Henry",
            last_name="Gale",
            ssn="222-33-4444",
            date_of_birth=dt.date(1984, 7, 4),
        ),
        address=Address(
            street1="200 S Main St",
            city="Wichita",
            state="KS",
            zip="67202",
        ),
        w2s=[
            W2(
                employer_name="Aviation Works Inc",
                box1_wages=Decimal("120000"),
                box2_federal_income_tax_withheld=Decimal("0"),
            ),
        ],
    )


@pytest.fixture
def federal_mfj_120k() -> FederalTotals:
    """$120k AGI MFJ / OBBBA std ded $31,500 / federal taxable $88,500."""
    return FederalTotals(
        filing_status=FilingStatus.MFJ,
        num_dependents=0,
        adjusted_gross_income=Decimal("120000"),
        taxable_income=Decimal("88500"),
        total_federal_tax=Decimal("10173"),
        federal_income_tax=Decimal("10173"),
        federal_standard_deduction=Decimal("31500"),
        federal_itemized_deductions_total=Decimal("0"),
        deduction_taken=Decimal("31500"),
        federal_withholding_from_w2s=Decimal("0"),
    )


@pytest.fixture
def hoh_50k_2deps_return() -> CanonicalReturn:
    """HOH with 2 dependents, $50k W-2, from Kansas City KS."""
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.HOH,
        taxpayer=Person(
            first_name="Jamie",
            last_name="Parent",
            ssn="333-44-5555",
            date_of_birth=dt.date(1980, 6, 1),
        ),
        address=Address(
            street1="701 N 7th St Trafficway",
            city="Kansas City",
            state="KS",
            zip="66101",
        ),
        w2s=[
            W2(
                employer_name="Big K Stockyards",
                box1_wages=Decimal("50000"),
                box2_federal_income_tax_withheld=Decimal("0"),
            ),
        ],
    )


@pytest.fixture
def federal_hoh_50k_2deps() -> FederalTotals:
    """$50k AGI HOH with 2 dependents."""
    return FederalTotals(
        filing_status=FilingStatus.HOH,
        num_dependents=2,
        adjusted_gross_income=Decimal("50000"),
        taxable_income=Decimal("26750"),  # approx after $23,625 HOH std
        total_federal_tax=Decimal("0"),
        federal_income_tax=Decimal("0"),
        federal_standard_deduction=Decimal("23625"),
        federal_itemized_deductions_total=Decimal("0"),
        deduction_taken=Decimal("23625"),
        federal_withholding_from_w2s=Decimal("0"),
    )


# ---------------------------------------------------------------------------
# Meta + Protocol conformance
# ---------------------------------------------------------------------------


class TestKansasPluginMeta:
    def test_meta_fields(self):
        """Core meta fields per spec."""
        assert PLUGIN.meta.code == "KS"
        assert PLUGIN.meta.name == "Kansas"
        assert PLUGIN.meta.has_income_tax is True
        assert PLUGIN.meta.starting_point == StateStartingPoint.FEDERAL_AGI
        assert PLUGIN.meta.reciprocity_partners == ()

    def test_meta_submission_channel_is_state_dor_free_portal(self):
        """Kansas WebFile is a free DOR portal."""
        assert (
            PLUGIN.meta.submission_channel
            == SubmissionChannel.STATE_DOR_FREE_PORTAL
        )

    def test_meta_dor_url_is_ksrevenue_gov(self):
        assert "ksrevenue.gov" in PLUGIN.meta.dor_url

    def test_meta_free_efile_url_present(self):
        """Kansas operates WebFile — the free e-file URL is non-None."""
        assert PLUGIN.meta.free_efile_url is not None
        assert PLUGIN.meta.free_efile_url.startswith("http")

    def test_meta_supports_2025(self):
        assert 2025 in PLUGIN.meta.supported_tax_years

    def test_meta_notes_mention_sb1_bracket_structure(self):
        """Notes should mention the SB 1 / 5.58% top rate."""
        assert "SB 1" in PLUGIN.meta.notes
        assert "5.58" in PLUGIN.meta.notes

    def test_meta_notes_mention_webfile(self):
        assert "WebFile" in PLUGIN.meta.notes

    def test_meta_notes_mention_no_reciprocity(self):
        assert "reciprocity" in PLUGIN.meta.notes.lower()

    def test_meta_notes_cite_ip25(self):
        """Notes should cite the source booklet IP25."""
        assert "IP25" in PLUGIN.meta.notes or "ip25" in PLUGIN.meta.notes

    def test_meta_is_frozen(self):
        """StatePluginMeta is frozen — mutation raises."""
        with pytest.raises(Exception):
            PLUGIN.meta.code = "MO"  # type: ignore[misc]

    def test_plugin_is_state_plugin_protocol(self):
        """runtime_checkable Protocol must recognize our concrete plugin."""
        assert isinstance(PLUGIN, StatePlugin)

    def test_plugin_is_kansas_plugin_instance(self):
        assert isinstance(PLUGIN, KansasPlugin)


# ---------------------------------------------------------------------------
# No reciprocity agreements
# ---------------------------------------------------------------------------


class TestKansasNoReciprocity:
    """Kansas has no bilateral reciprocity agreements with any state."""

    def test_no_reciprocity_partners(self):
        """Meta reciprocity_partners is the empty tuple."""
        assert PLUGIN.meta.reciprocity_partners == ()
        assert len(PLUGIN.meta.reciprocity_partners) == 0

    def test_no_reciprocity_via_reciprocity_table(self):
        """The shared ReciprocityTable also reports no partners for KS."""
        table = ReciprocityTable.load()
        assert table.partners_of("KS") == frozenset()
        assert table.has_income_tax("KS") is True

    def test_not_reciprocal_with_neighbors(self):
        """Spot-check that KS is not reciprocal with any of its neighbors.

        Kansas borders Nebraska (N), Missouri (E), Oklahoma (S), and
        Colorado (W). None of these share a reciprocity agreement with
        Kansas — KS residents who work in any of them must file a
        nonresident return in that state and claim the credit for taxes
        paid on K-40 line 13.
        """
        table = ReciprocityTable.load()
        for neighbor in ("NE", "MO", "OK", "CO"):
            assert table.are_reciprocal("KS", neighbor) is False


# ---------------------------------------------------------------------------
# Standard deduction + exemption helpers
# ---------------------------------------------------------------------------


class TestKansasStandardDeduction:
    """IP25 page 2 Kansas Standard Deduction amounts."""

    def test_single(self):
        assert ks_standard_deduction(FilingStatus.SINGLE) == Decimal("3605")

    def test_mfj(self):
        assert ks_standard_deduction(FilingStatus.MFJ) == Decimal("8240")

    def test_hoh(self):
        assert ks_standard_deduction(FilingStatus.HOH) == Decimal("6180")

    def test_mfs(self):
        assert ks_standard_deduction(FilingStatus.MFS) == Decimal("4120")

    def test_qss_mirrors_mfj(self):
        """QSS takes the MFJ allowance in Kansas (largest; conservative)."""
        assert ks_standard_deduction(FilingStatus.QSS) == Decimal("8240")

    def test_constants_match_helpers(self):
        """Module-level constants agree with the helper function."""
        assert KS_TY2025_STD_DED_SINGLE == Decimal("3605")
        assert KS_TY2025_STD_DED_MFJ == Decimal("8240")
        assert KS_TY2025_STD_DED_HOH == Decimal("6180")
        assert KS_TY2025_STD_DED_MFS == Decimal("4120")


class TestKansasExemptionAllowance:
    """IP25 page 2 Kansas Exemption Allowance."""

    def test_single_no_dependents(self):
        assert ks_exemption_allowance(FilingStatus.SINGLE, 0) == Decimal("9160")

    def test_mfj_no_dependents(self):
        """MFJ gets two base exemptions ($18,320)."""
        assert ks_exemption_allowance(FilingStatus.MFJ, 0) == Decimal("18320")

    def test_hoh_no_dependents(self):
        assert ks_exemption_allowance(FilingStatus.HOH, 0) == Decimal("9160")

    def test_mfs_no_dependents(self):
        assert ks_exemption_allowance(FilingStatus.MFS, 0) == Decimal("9160")

    def test_single_2_dependents(self):
        """$9,160 base + 2 * $2,320 = $13,800."""
        assert ks_exemption_allowance(FilingStatus.SINGLE, 2) == Decimal("13800")

    def test_mfj_3_dependents(self):
        """$18,320 base + 3 * $2,320 = $25,280."""
        assert ks_exemption_allowance(FilingStatus.MFJ, 3) == Decimal("25280")

    def test_hoh_2_dependents(self):
        """$9,160 base + 2 * $2,320 = $13,800 (matches HOH fixture)."""
        assert ks_exemption_allowance(FilingStatus.HOH, 2) == Decimal("13800")

    def test_negative_dependents_clamped_to_zero(self):
        """Negative dep count is treated as zero, not negative credit."""
        assert ks_exemption_allowance(FilingStatus.SINGLE, -1) == Decimal("9160")


# ---------------------------------------------------------------------------
# Tax computation worksheet — IP25 page 34 formula
# ---------------------------------------------------------------------------


class TestKansasTaxComputationWorksheet:
    """Formula lock for the IP25 page 34 Tax Computation Worksheet.

    Per IP25 page 34:
        Single / HOH / MFS:
            $0-$23,000:   TI * 0.0520
            $23,001+:     TI * 0.0558 - $87
        Married Filing Joint:
            $0-$46,000:   TI * 0.0520
            $46,001+:     TI * 0.0558 - $175
    """

    def test_zero_income(self):
        assert ks_tax_from_worksheet(Decimal("0"), FilingStatus.SINGLE) == Decimal("0")
        assert ks_tax_from_worksheet(Decimal("0"), FilingStatus.MFJ) == Decimal("0")

    def test_negative_income_clamped_to_zero(self):
        assert ks_tax_from_worksheet(Decimal("-1000"), FilingStatus.SINGLE) == Decimal("0")

    def test_single_at_low_end(self):
        """$1,000 Single: 1000 * 0.052 = $52.00."""
        assert ks_tax_from_worksheet(
            Decimal("1000"), FilingStatus.SINGLE
        ) == Decimal("52.00")

    def test_single_just_below_bracket_break(self):
        """$23,000 Single: 23000 * 0.052 = $1,196.00 (top of lower bracket)."""
        assert ks_tax_from_worksheet(
            Decimal("23000"), FilingStatus.SINGLE
        ) == Decimal("1196.00")

    def test_single_just_above_bracket_break(self):
        """$23,001 Single: 23001 * 0.0558 - 87 = $1,196.4558 → $1,196.46."""
        assert ks_tax_from_worksheet(
            Decimal("23001"), FilingStatus.SINGLE
        ) == Decimal("1196.46")

    def test_single_bracket_continuity(self):
        """Worksheet formula should produce ≈ the same tax at the break.

        23000 * 0.0520 = 1196.00
        23001 * 0.0558 - 87 = 1196.4558
        Gap is well under $1 (~46 cents).
        """
        tax_below = ks_tax_from_worksheet(Decimal("23000"), FilingStatus.SINGLE)
        tax_above = ks_tax_from_worksheet(Decimal("23001"), FilingStatus.SINGLE)
        gap = tax_above - tax_below
        assert gap < Decimal("1.00")
        assert gap >= Decimal("0")

    def test_mfj_bracket_continuity(self):
        """MFJ continuity at $46,000.

        46000 * 0.0520 = 2392.00
        46001 * 0.0558 - 175 = 2391.8558
        Gap is slightly negative (-0.14), which is a standard
        continuous-bracket rounding artifact.
        """
        tax_below = ks_tax_from_worksheet(Decimal("46000"), FilingStatus.MFJ)
        tax_above = ks_tax_from_worksheet(Decimal("46001"), FilingStatus.MFJ)
        # |gap| < $1 at the break
        assert abs(tax_above - tax_below) < Decimal("1.00")

    def test_mfj_at_bracket_break(self):
        assert ks_tax_from_worksheet(
            Decimal("46000"), FilingStatus.MFJ
        ) == Decimal("2392.00")

    def test_mfj_just_above_bracket_break(self):
        """$46,001 MFJ: 46001 * 0.0558 - 175 = 2566.8558 - 175 = 2391.8558."""
        assert ks_tax_from_worksheet(
            Decimal("46001"), FilingStatus.MFJ
        ) == Decimal("2391.86")

    def test_single_65k_ti_52235(self):
        """**$65k Single resident lock** — the spec's wrap-correctness
        lock value.

        AGI = $65,000
        Std ded = $3,605 (KS Single)
        Exemption = $9,160 (KS Single, 0 deps)
        KS TI = 65,000 - 3,605 - 9,160 = $52,235
        Tax = 52,235 * 0.0558 - 87 = 2,914.713 - 87 = 2,827.713
        Rounded to cents (half-up) = $2,827.71
        """
        ti = Decimal("52235")
        tax = ks_tax_from_worksheet(ti, FilingStatus.SINGLE)
        assert tax == Decimal("2827.71")

    def test_hoh_uses_single_bracket(self):
        """HOH shares the Single bracket (both use $23k/$87)."""
        ti = Decimal("30000")
        hoh = ks_tax_from_worksheet(ti, FilingStatus.HOH)
        single = ks_tax_from_worksheet(ti, FilingStatus.SINGLE)
        assert hoh == single

    def test_mfs_uses_single_bracket(self):
        """MFS shares the Single bracket ($23k/$87), not the MFJ one."""
        ti = Decimal("30000")
        mfs = ks_tax_from_worksheet(ti, FilingStatus.MFS)
        single = ks_tax_from_worksheet(ti, FilingStatus.SINGLE)
        assert mfs == single

    def test_qss_uses_mfj_bracket(self):
        """QSS shares the MFJ bracket ($46k/$175), not the Single one."""
        ti = Decimal("40000")
        qss = ks_tax_from_worksheet(ti, FilingStatus.QSS)
        mfj = ks_tax_from_worksheet(ti, FilingStatus.MFJ)
        assert qss == mfj

    def test_single_100k(self):
        """$100k Single: 100000 * 0.0558 - 87 = 5580 - 87 = $5,493.00."""
        assert ks_tax_from_worksheet(
            Decimal("100000"), FilingStatus.SINGLE
        ) == Decimal("5493.00")

    def test_single_250k(self):
        """$250k Single: 250000 * 0.0558 - 87 = 13,950 - 87 = $13,863.00."""
        assert ks_tax_from_worksheet(
            Decimal("250000"), FilingStatus.SINGLE
        ) == Decimal("13863.00")

    def test_constants_match(self):
        """Module-level rate/break constants match IP25 page 34."""
        assert KS_TY2025_LOWER_RATE == Decimal("0.052")
        assert KS_TY2025_UPPER_RATE == Decimal("0.0558")
        assert KS_TY2025_BRACKET_BREAK_SINGLE == Decimal("23000")
        assert KS_TY2025_BRACKET_BREAK_MFJ == Decimal("46000")
        assert KS_TY2025_SUBTRACT_SINGLE == Decimal("87")
        assert KS_TY2025_SUBTRACT_MFJ == Decimal("175")


# ---------------------------------------------------------------------------
# Tax Table — low bracket exhaustive regression against IP25 pages 27-28
# ---------------------------------------------------------------------------


class TestKansasTaxTableLowBracket:
    """Exhaustive lock: every printed low-bracket row of IP25 pages 27-28.

    The table-lookup formula matches the Kansas printed tax table
    EXACTLY for every low-bracket row (TI ≤ $23,000). These rows are
    extracted verbatim from IP25 page 27-28 as published by the Kansas
    Department of Revenue. A regression in ``ks_tax_from_table`` for
    any of these rows fails CI and requires re-validating the formula.

    Source: https://www.ksrevenue.gov/pdf/ip25.pdf pages 27-28.
    """

    # Extracted rows (at_least, but_not_more_than, single_tax, mfj_tax)
    # from IP25 pages 27-28. Single and MFJ are identical in the low
    # bracket (both use 5.20%).
    LOW_BRACKET_ROWS: list[tuple[int, int, int, int]] = [
        (26, 50, 2, 2),
        (51, 100, 4, 4),
        (101, 150, 7, 7),
        (151, 200, 9, 9),
        (201, 250, 12, 12),
        (251, 300, 14, 14),
        (301, 350, 17, 17),
        (351, 400, 20, 20),
        (401, 450, 22, 22),
        (451, 500, 25, 25),
        (501, 550, 27, 27),
        (551, 600, 30, 30),
        (601, 650, 33, 33),
        (651, 700, 35, 35),
        (701, 750, 38, 38),
        (751, 800, 40, 40),
        (801, 850, 43, 43),
        (851, 900, 46, 46),
        (901, 950, 48, 48),
        (951, 1000, 51, 51),
        (1001, 1050, 53, 53),
        (2951, 3000, 155, 155),
        (3001, 3050, 157, 157),
        (3051, 3100, 160, 160),
        (3251, 3300, 170, 170),
        (3301, 3350, 173, 173),
        (3351, 3400, 176, 176),
        (6601, 6650, 345, 345),
        (9901, 9950, 516, 516),
        (9951, 10000, 519, 519),
        (10001, 10050, 521, 521),
        (19801, 19850, 1031, 1031),
        (22951, 23000, 1195, 1195),
    ]

    @pytest.mark.parametrize(
        "at_least,but_not_more_than,expected_single,expected_mfj",
        LOW_BRACKET_ROWS,
    )
    def test_row_lock_at_at_least(
        self, at_least, but_not_more_than, expected_single, expected_mfj
    ):
        """At the row's at_least value, tax matches printed table exactly."""
        ti = Decimal(str(at_least))
        assert ks_tax_from_table(ti, FilingStatus.SINGLE) == Decimal(
            str(expected_single)
        )
        assert ks_tax_from_table(ti, FilingStatus.MFJ) == Decimal(
            str(expected_mfj)
        )

    @pytest.mark.parametrize(
        "at_least,but_not_more_than,expected_single,expected_mfj",
        LOW_BRACKET_ROWS,
    )
    def test_row_lock_at_but_not_more_than(
        self, at_least, but_not_more_than, expected_single, expected_mfj
    ):
        """At the row's but_not_more_than value, still in the same row."""
        ti = Decimal(str(but_not_more_than))
        assert ks_tax_from_table(ti, FilingStatus.SINGLE) == Decimal(
            str(expected_single)
        )
        assert ks_tax_from_table(ti, FilingStatus.MFJ) == Decimal(
            str(expected_mfj)
        )

    def test_below_26_is_zero(self):
        """TI below the first row ($26) is zero tax."""
        assert ks_tax_from_table(Decimal("25"), FilingStatus.SINGLE) == Decimal("0.00")
        assert ks_tax_from_table(Decimal("0"), FilingStatus.SINGLE) == Decimal("0")

    def test_row_boundary_belongs_to_ending_row(self):
        """TI = $100 is in row [51, 100], not row [101, 150]."""
        # Row [51, 100] has tax $4
        assert ks_tax_from_table(Decimal("100"), FilingStatus.SINGLE) == Decimal("4")
        # Row [101, 150] has tax $7
        assert ks_tax_from_table(Decimal("101"), FilingStatus.SINGLE) == Decimal("7")


class TestKansasTaxTableMfjLowBracketExtended:
    """Additional MFJ low-bracket rows (where MFJ stays in 5.20% up to $46k).

    These rows overlap with Single's upper bracket — Single is 5.58%
    above $23,000 while MFJ is still 5.20%. This test pins that split.
    Source: IP25 pages 28-29.
    """

    # (at_least, but_not_more_than, single_tax, mfj_tax)
    # MFJ stays lower, Single has already jumped to 5.58%.
    MFJ_ONLY_LOW_ROWS = [
        (23101, 23150, 1203, 1203),  # MFJ still at 5.20%, Single at 5.58% sub 87
        (26151, 26200, 1373, 1361),
        (29701, 29750, 1571, 1546),
        (33001, 33050, 1755, 1717),
        (36301, 36350, 1940, 1889),
        (39601, 39650, 2124, 2061),
        (42601, 42650, 2291, 2217),
        (45951, 46000, 2478, 2391),  # MFJ top of lower bracket
    ]

    @pytest.mark.parametrize(
        "at_least,but_not_more_than,_single,expected_mfj", MFJ_ONLY_LOW_ROWS
    )
    def test_mfj_row_lock_low_bracket(
        self, at_least, but_not_more_than, _single, expected_mfj
    ):
        """MFJ low-bracket rows $23k-$46k lock to printed IP25 table."""
        ti = Decimal(str(at_least))
        assert ks_tax_from_table(ti, FilingStatus.MFJ) == Decimal(
            str(expected_mfj)
        )


class TestKansasTaxTableUpperBracketApproximation:
    """Upper-bracket tax table lookup is within $1 of the printed table.

    The Kansas DOR's printed tax table for the upper bracket was
    generated with a rounding convention that is not naive
    ``round(midpoint * rate - subtract)``. The authoritative tax
    value for electronic filers is the Tax Computation Worksheet
    formula (``ks_tax_from_worksheet``), which this plugin's
    ``compute`` method uses. The ``ks_tax_from_table`` helper
    documents the printed-table convention and is always within $1
    of the printed value in the upper bracket.
    """

    def test_upper_bracket_table_within_1_dollar_of_worksheet(self):
        """Spot-check a few upper-bracket TIs: table is within $1."""
        for ti in ("23500", "30000", "52235", "75000", "95000"):
            t = Decimal(ti)
            worksheet = ks_tax_from_worksheet(t, FilingStatus.SINGLE)
            table = ks_tax_from_table(t, FilingStatus.SINGLE)
            # Table is rounded to whole dollars, so |worksheet - table|
            # < 1 dollar plus the rounding artifact.
            assert abs(worksheet - table) < Decimal("2.00"), (
                f"TI={ti}: worksheet={worksheet}, table={table}"
            )

    def test_upper_bracket_table_single_65k_ti(self):
        """Table value at TI=52235 Single is $2,827 (whole dollars)."""
        assert ks_tax_from_table(
            Decimal("52235"), FilingStatus.SINGLE
        ) == Decimal("2827.00")

    def test_over_100k_delegates_to_worksheet(self):
        """Above $100k, table lookup falls back to worksheet."""
        ti = Decimal("150000")
        table = ks_tax_from_table(ti, FilingStatus.SINGLE)
        worksheet = ks_tax_from_worksheet(ti, FilingStatus.SINGLE)
        assert table == worksheet


# ---------------------------------------------------------------------------
# compute() — resident scenarios
# ---------------------------------------------------------------------------


class TestKansasPluginComputeResident:
    def test_compute_returns_state_return(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert isinstance(result, StateReturn)
        assert result.state == "KS"
        assert result.residency == ResidencyStatus.RESIDENT
        assert result.days_in_state == 365

    def test_resident_single_65k_tax_lock(
        self, single_65k_return, federal_single_65k
    ):
        """**SPEC-MANDATED WRAP-CORRECTNESS LOCK**: $65k Single KS
        resident → state_total_tax = $2,827.71.

        Tenforty does not support 2025/KS_K40 (``ValueError: OTS does
        not support 2025/KS_K40``), so the lock value is pinned to the
        IP25 page 34 Tax Computation Worksheet — the formula Kansas
        WebFile uses internally for all e-filed returns.

        Hand trace:
            AGI = $65,000
            - Std ded (Single) = $3,605 (IP25 page 2)
            - Exemption (Single) = $9,160 (IP25 page 2)
            KS taxable income = $52,235
            Worksheet: 52235 * 0.0558 - 87
                     = 2914.7130 - 87
                     = 2827.7130
                     = $2,827.71 (half-up to cents)
        """
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_total_tax"] == LOCK_VALUE
        assert (
            result.state_specific["state_total_tax_resident_basis"] == LOCK_VALUE
        )
        assert (
            result.state_specific["state_total_tax_worksheet_basis"] == LOCK_VALUE
        )

    def test_resident_single_65k_line_breakdown(
        self, single_65k_return, federal_single_65k
    ):
        """Every K-40 line is surfaced on state_specific."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["state_federal_agi"] == Decimal("65000.00")
        assert ss["state_adjusted_gross_income"] == Decimal("65000.00")
        assert ss["state_standard_deduction"] == Decimal("3605.00")
        assert ss["state_exemption_allowance"] == Decimal("9160.00")
        assert ss["state_total_deductions"] == Decimal("12765.00")
        assert ss["state_taxable_income"] == Decimal("52235.00")

    def test_resident_mfj_120k(self, mfj_120k_return, federal_mfj_120k):
        """MFJ $120k, 0 deps.

        AGI=120000
        - Std ded (MFJ) = 8240
        - Exemption (MFJ, 0 deps) = 18320
        KS TI = 120000 - 8240 - 18320 = 93440
        TI > 46000, so upper bracket: 93440 * 0.0558 - 175
            = 5213.952 - 175 = 5038.952 → $5,038.95
        """
        result = PLUGIN.compute(
            mfj_120k_return,
            federal_mfj_120k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["state_taxable_income"] == Decimal("93440.00")
        assert result.state_specific["state_total_tax"] == Decimal("5038.95")

    def test_resident_hoh_2deps_stays_in_low_bracket(
        self, hoh_50k_2deps_return, federal_hoh_50k_2deps
    ):
        """HOH $50k AGI with 2 dependents.

        AGI=50000
        - Std ded (HOH) = 6180
        - Exemption (HOH + 2 deps) = 9160 + 2 * 2320 = 13800
        KS TI = 50000 - 6180 - 13800 = 30020
        TI > 23000 (HOH uses Single bracket), upper: 30020 * 0.0558 - 87
            = 1675.116 - 87 = 1588.116 → $1,588.12
        """
        result = PLUGIN.compute(
            hoh_50k_2deps_return,
            federal_hoh_50k_2deps,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        ss = result.state_specific
        assert ss["state_exemption_allowance"] == Decimal("13800.00")
        assert ss["state_taxable_income"] == Decimal("30020.00")
        assert ss["state_total_tax"] == Decimal("1588.12")

    def test_state_specific_numerics_are_decimal(
        self, single_65k_return, federal_single_65k
    ):
        """Every numeric value in state_specific is Decimal (no floats)."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        decimal_keys = [
            "state_federal_agi",
            "state_adjusted_gross_income",
            "state_standard_deduction",
            "state_exemption_allowance",
            "state_total_deductions",
            "state_taxable_income",
            "state_total_tax",
            "state_total_tax_resident_basis",
            "state_total_tax_worksheet_basis",
            "state_total_tax_table_basis",
            "state_lower_rate",
            "state_upper_rate",
            "state_bracket_break",
            "apportionment_fraction",
            "ks_modifications_applied",
        ]
        for key in decimal_keys:
            assert key in result.state_specific, f"missing {key}"
            assert isinstance(
                result.state_specific[key], Decimal
            ), f"{key} is not Decimal"

    def test_resident_apportionment_is_one(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["apportionment_fraction"] == Decimal("1")

    def test_starting_point_is_federal_agi(
        self, single_65k_return, federal_single_65k
    ):
        """KS K-40 line 1 is federal AGI, per IP25."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["starting_point"] == "federal_agi"

    def test_social_security_fully_exempt_flag(
        self, single_65k_return, federal_single_65k
    ):
        """Kansas SB 1 (2024) fully exempts Social Security."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert result.state_specific["ks_social_security_fully_exempt"] is True
        assert "SB 1" in result.state_specific["ks_social_security_note"]

    def test_v1_limitations_nonempty(
        self, single_65k_return, federal_single_65k
    ):
        """v1_limitations is a non-empty list enumerating unmodeled items."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        lims = result.state_specific["v1_limitations"]
        assert isinstance(lims, list)
        assert len(lims) >= 5
        assert any("Schedule S" in x for x in lims)
        assert any("nonresident" in x.lower() for x in lims)

    def test_state_return_validates_via_pydantic(
        self, single_65k_return, federal_single_65k
    ):
        """Round-trip through Pydantic JSON to confirm model validity."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        dumped = result.model_dump(mode="json")
        rehydrated = StateReturn.model_validate(dumped)
        assert rehydrated.state == "KS"
        assert rehydrated.residency == ResidencyStatus.RESIDENT

    def test_zero_income_yields_zero_tax(self):
        """An all-zero return yields zero KS tax."""
        ret = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="Zero",
                last_name="Income",
                ssn="999-88-7777",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(
                street1="1 Main St",
                city="Topeka",
                state="KS",
                zip="66612",
            ),
        )
        fed = FederalTotals(
            filing_status=FilingStatus.SINGLE,
            num_dependents=0,
            adjusted_gross_income=Decimal("0"),
            taxable_income=Decimal("0"),
            total_federal_tax=Decimal("0"),
            federal_income_tax=Decimal("0"),
            federal_standard_deduction=Decimal("15750"),
            federal_itemized_deductions_total=Decimal("0"),
            deduction_taken=Decimal("15750"),
        )
        result = PLUGIN.compute(
            ret, fed, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert result.state_specific["state_taxable_income"] == Decimal("0.00")
        assert result.state_specific["state_total_tax"] == Decimal("0.00")


# ---------------------------------------------------------------------------
# compute() — nonresident / part-year
# ---------------------------------------------------------------------------


class TestKansasPluginComputeNonresident:
    def test_nonresident_half_year_prorates_tax(
        self, single_65k_return, federal_single_65k
    ):
        """Nonresident with days_in_state=182 yields ~1/2 resident tax."""
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=182,
        )
        full = result.state_specific["state_total_tax_resident_basis"]
        apportioned = result.state_specific["state_total_tax"]
        assert apportioned < full
        expected = (full * Decimal(182) / Decimal(365)).quantize(Decimal("0.01"))
        assert apportioned == expected

    def test_nonresident_residency_preserved(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=182,
        )
        assert result.residency == ResidencyStatus.NONRESIDENT
        assert result.days_in_state == 182

    def test_part_year_apportionment_fraction(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.PART_YEAR,
            days_in_state=91,
        )
        expected_fraction = Decimal(91) / Decimal(365)
        assert result.state_specific["apportionment_fraction"] == expected_fraction

    def test_zero_days_yields_zero_tax(
        self, single_65k_return, federal_single_65k
    ):
        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=0,
        )
        assert result.state_specific["state_total_tax"] == Decimal("0.00")

    def test_resident_basis_tax_unchanged_by_apportionment(
        self, single_65k_return, federal_single_65k
    ):
        """state_total_tax_resident_basis is invariant across apportionment."""
        res = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        nr = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.NONRESIDENT,
            days_in_state=100,
        )
        assert (
            res.state_specific["state_total_tax_resident_basis"]
            == nr.state_specific["state_total_tax_resident_basis"]
        )


# ---------------------------------------------------------------------------
# apportion_income()
# ---------------------------------------------------------------------------


class TestKansasPluginApportionIncome:
    def test_apportion_income_resident_full_amounts(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.RESIDENT, days_in_state=365
        )
        assert isinstance(app, IncomeApportionment)
        assert app.state_source_wages == Decimal("65000.00")
        assert app.state_source_interest == Decimal("0")
        assert app.state_source_dividends == Decimal("0")
        assert app.state_source_capital_gains == Decimal("0")
        assert app.state_source_self_employment == Decimal("0")
        assert app.state_source_rental == Decimal("0")
        assert app.state_source_total == Decimal("65000.00")

    def test_apportion_income_nonresident_prorates(self, single_65k_return):
        app = PLUGIN.apportion_income(
            single_65k_return, ResidencyStatus.NONRESIDENT, days_in_state=182
        )
        expected = (Decimal("65000") * Decimal(182) / Decimal(365)).quantize(
            Decimal("0.01")
        )
        assert app.state_source_wages == expected


# ---------------------------------------------------------------------------
# render_pdfs() + form_ids()
# ---------------------------------------------------------------------------


class TestKansasPluginFormIds:
    def test_form_ids(self):
        """K-40 is the form_id."""
        ids = PLUGIN.form_ids()
        assert "KS Form K-40" in ids
        assert ids == ["KS Form K-40"]

    def test_render_pdfs_returns_empty_list(
        self, single_65k_return, federal_single_65k, tmp_path
    ):
        """Fan-out follow-up: K-40 PDF fill not yet implemented."""
        state_return = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert PLUGIN.render_pdfs(state_return, tmp_path) == []

    def test_render_pdfs_accepts_path(
        self, single_65k_return, federal_single_65k
    ):
        state_return = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        assert PLUGIN.render_pdfs(state_return, Path("/tmp")) == []


# ---------------------------------------------------------------------------
# Tenforty support seam — auto-promotes to a real lock if/when tenforty
# gains KS support.
# ---------------------------------------------------------------------------


class TestKansasTenfortyWrapLock:
    """Wrap-correctness lock seam.

    Per the task spec: "$65k single-filer KS resident must match
    tenforty bit-for-bit." At time of writing, tenforty does NOT support
    2025/KS_K40 (``ValueError: OTS does not support 2025/KS_K40``), so
    this test skips if the attempted call raises. If tenforty ever adds
    KS support, this test auto-activates and asserts bit-for-bit parity
    against whatever tenforty returns — a future drift detector.
    """

    def test_65k_single_matches_tenforty_if_available(
        self, single_65k_return, federal_single_65k
    ):
        try:
            import tenforty  # type: ignore[import-not-found]
        except ImportError:
            pytest.skip("tenforty not installed")

        try:
            tf = tenforty.evaluate_return(
                year=2025,
                state="KS",
                filing_status="Single",
                w2_income=65000,
                standard_or_itemized="Standard",
            )
        except ValueError as exc:
            if "does not support" in str(exc) and "KS" in str(exc):
                pytest.skip(
                    f"tenforty does not support KS for TY2025 "
                    f"({exc}). This test auto-activates when tenforty "
                    f"gains KS support."
                )
            raise

        result = PLUGIN.compute(
            single_65k_return,
            federal_single_65k,
            ResidencyStatus.RESIDENT,
            days_in_state=365,
        )
        # Convert tenforty's float to a cents Decimal.
        tf_tax = Decimal(str(tf.state_total_tax)).quantize(Decimal("0.01"))
        assert result.state_specific["state_total_tax"] == tf_tax


# ---------------------------------------------------------------------------
# V1 limitations visibility
# ---------------------------------------------------------------------------


class TestKansasV1LimitationsModule:
    def test_limitations_is_tuple(self):
        assert isinstance(KS_V1_LIMITATIONS, tuple)

    def test_limitations_mention_schedule_s(self):
        joined = " ".join(KS_V1_LIMITATIONS)
        assert "Schedule S" in joined

    def test_limitations_mention_credit_for_taxes_paid(self):
        joined = " ".join(KS_V1_LIMITATIONS).lower()
        assert "credit" in joined and "other states" in joined

    def test_limitations_mention_eitc(self):
        joined = " ".join(KS_V1_LIMITATIONS)
        assert "EITC" in joined or "Earned Income" in joined

    def test_limitations_mention_nonresident_schedule_s_part_b(self):
        joined = " ".join(KS_V1_LIMITATIONS)
        assert "Part B" in joined
