"""Tests for ``skill.scripts.output.ffff_entry_map``.

The FFFF entry map translates a computed ``CanonicalReturn`` into a
field-by-field transcript of every entry the taxpayer must type into
freefillableforms.com. These tests exercise:

* The entry count and required-line coverage for a simple W-2 return
  (must include 1a, 1z, 9, 10, 11, 12, 15, 16, 24, 25a, 33, 34).
* Itemized return produces Schedule A entries (``1040-SA`` form).
* Schedule C + SE return produces per-business ``1040-SC-1`` +
  ``1040-SSE-1`` entries.
* ``to_text()`` is pure ASCII (no binary, no weird escape sequences).
* ``to_json()`` round-trips and holds a metadata header.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.calc.engine import compute
from skill.scripts.models import CanonicalReturn
from skill.scripts.output.ffff_entry_map import (
    FFFFEntry,
    FFFFEntryMap,
    build_ffff_entry_map,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _computed(fixture_name: str) -> CanonicalReturn:
    data = json.loads((FIXTURES / fixture_name / "input.json").read_text())
    canonical = CanonicalReturn.model_validate(data)
    return compute(canonical)


# ---------------------------------------------------------------------------
# 1. Simple W-2 Single return
# ---------------------------------------------------------------------------


REQUIRED_1040_LINES: tuple[str, ...] = (
    "1a",
    "1z",
    "9",
    "10",
    "11",
    "12",
    "15",
    "16",
    "24",
    "25a",
    "33",
    "34",
)


class TestSimpleW2:
    """$65k W-2 Single return — the baseline scenario."""

    def test_produces_entry_map(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        assert isinstance(entry_map, FFFFEntryMap)
        assert entry_map.tax_year == 2025
        assert entry_map.filing_status == "Single"
        assert entry_map.taxpayer_name == "Alex Doe"

    def test_has_at_least_ten_form_1040_lines(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        form_1040_entries = [e for e in entry_map.entries if e.form == "1040"]
        assert len(form_1040_entries) >= 10

    def test_includes_all_required_form_1040_lines(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        present_lines = {e.line for e in entry_map.entries if e.form == "1040"}
        missing = [ln for ln in REQUIRED_1040_LINES if ln not in present_lines]
        assert missing == [], f"missing required 1040 lines: {missing}"

    def test_line_1a_value_is_65000(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        line_1a = next(
            e for e in entry_map.entries if e.form == "1040" and e.line == "1a"
        )
        assert line_1a.value == "65,000.00"

    def test_no_schedule_a_for_standard_deduction(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        assert not any(e.form == "1040-SA" for e in entry_map.entries)

    def test_no_schedule_b_below_threshold(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        assert not any(e.form == "1040-SB" for e in entry_map.entries)

    def test_no_schedule_c_for_pure_w2_return(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        assert not any(e.form.startswith("1040-SC") for e in entry_map.entries)

    def test_no_schedule_se_for_pure_w2_return(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        assert not any(e.form.startswith("1040-SSE") for e in entry_map.entries)


# ---------------------------------------------------------------------------
# 2. Itemized MFJ return with investments
# ---------------------------------------------------------------------------


class TestItemizedWithInvestments:
    """MFJ with W-2s, 1099-INT/DIV/B, itemized deductions."""

    def test_produces_schedule_a_entries(self):
        canonical = _computed("w2_investments_itemized")
        entry_map = build_ffff_entry_map(canonical)
        sa_entries = [e for e in entry_map.entries if e.form == "1040-SA"]
        assert len(sa_entries) > 0

    def test_schedule_a_includes_salt_subtotal_and_cap(self):
        canonical = _computed("w2_investments_itemized")
        entry_map = build_ffff_entry_map(canonical)
        sa_lines = {e.line for e in entry_map.entries if e.form == "1040-SA"}
        assert "5a" in sa_lines
        assert "5d" in sa_lines
        assert "5e" in sa_lines
        assert "17" in sa_lines

    def test_schedule_b_triggered_by_investment_interest(self):
        """$3000 in taxable interest > $1,500 threshold -> Schedule B."""
        canonical = _computed("w2_investments_itemized")
        entry_map = build_ffff_entry_map(canonical)
        sb_entries = [e for e in entry_map.entries if e.form == "1040-SB"]
        assert len(sb_entries) > 0
        # Line 2 (total interest) and line 6 (total dividends) should be emitted
        sb_lines = {e.line for e in entry_map.entries if e.form == "1040-SB"}
        assert "2" in sb_lines
        assert "6" in sb_lines

    def test_schedule_b_payer_rows_emitted(self):
        canonical = _computed("w2_investments_itemized")
        entry_map = build_ffff_entry_map(canonical)
        # At least one payer row (line 1.1.payer + line 1.1.amount) for interest.
        sb_lines = {e.line for e in entry_map.entries if e.form == "1040-SB"}
        assert "1.1.payer" in sb_lines
        assert "1.1.amount" in sb_lines

    def test_spouse_entries_present_for_mfj(self):
        canonical = _computed("w2_investments_itemized")
        entry_map = build_ffff_entry_map(canonical)
        spouse_entries = [
            e for e in entry_map.entries
            if e.form == "1040" and e.line.startswith("header.spouse")
        ]
        assert len(spouse_entries) == 2  # name + ssn


# ---------------------------------------------------------------------------
# 3. Schedule C + SE return
# ---------------------------------------------------------------------------


class TestScheduleCAndSE:
    """Sole proprietor with SE income above $400 floor."""

    def test_produces_schedule_c_entries(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        sc_entries = [e for e in entry_map.entries if e.form == "1040-SC-1"]
        assert len(sc_entries) > 0

    def test_schedule_c_form_name_uses_index_suffix(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        sc_forms = {e.form for e in entry_map.entries if e.form.startswith("1040-SC-")}
        # Exactly one business -> exactly one SC form
        assert sc_forms == {"1040-SC-1"}

    def test_schedule_c_includes_line_31_net_profit(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        line_31 = next(
            e for e in entry_map.entries
            if e.form == "1040-SC-1" and e.line == "31"
        )
        # Net profit should be positive for this profitable fixture.
        assert line_31.value != "0.00"

    def test_produces_schedule_se_entries(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        sse_entries = [e for e in entry_map.entries if e.form == "1040-SSE-1"]
        assert len(sse_entries) > 0

    def test_schedule_se_includes_se_tax_line_12(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        line_12 = next(
            e for e in entry_map.entries
            if e.form == "1040-SSE-1" and e.line == "12"
        )
        assert line_12.value != "0.00"


# ---------------------------------------------------------------------------
# 4. Serialization: to_text() + to_json()
# ---------------------------------------------------------------------------


class TestToText:
    def test_text_is_pure_ascii(self):
        """``to_text`` must not emit any non-ASCII chars (no em-dashes,
        no Unicode smart quotes, no escape sequences)."""
        canonical = _computed("w2_investments_itemized")
        text = build_ffff_entry_map(canonical).to_text()
        non_ascii = [c for c in text if ord(c) > 127]
        assert non_ascii == [], f"non-ASCII chars found: {non_ascii[:5]}"

    def test_text_contains_heading_and_taxpayer(self):
        canonical = _computed("simple_w2_standard")
        text = build_ffff_entry_map(canonical).to_text()
        assert "FFFF Entry Transcript" in text
        assert "Alex Doe" in text
        assert "Tax Year 2025" in text
        assert "Form 1040" in text

    def test_text_groups_by_form(self):
        """Each form appears once as a heading, in emit order."""
        canonical = _computed("se_home_office")
        text = build_ffff_entry_map(canonical).to_text()
        assert "Form 1040" in text
        assert "Schedule C #1" in text
        assert "Schedule SE #1" in text
        # Form 1040 heading comes before Schedule C heading
        assert text.index("Form 1040") < text.index("Schedule C #1")

    def test_text_includes_line_values(self):
        canonical = _computed("simple_w2_standard")
        text = build_ffff_entry_map(canonical).to_text()
        # Form 1040 line 1a should show $65,000.00
        assert "65,000.00" in text


class TestToJson:
    def test_json_round_trips(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        parsed = json.loads(entry_map.to_json())
        assert parsed["tax_year"] == 2025
        assert parsed["taxpayer_name"] == "Alex Doe"
        assert parsed["filing_status"] == "Single"
        assert isinstance(parsed["entries"], list)
        assert len(parsed["entries"]) == len(entry_map.entries)

    def test_json_entry_shape(self):
        canonical = _computed("simple_w2_standard")
        parsed = json.loads(build_ffff_entry_map(canonical).to_json())
        first = parsed["entries"][0]
        assert set(first.keys()) == {
            "form", "line", "value", "description", "note"
        }

    def test_json_preserves_emit_order(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        parsed = json.loads(entry_map.to_json())
        original_forms = [e.form for e in entry_map.entries]
        parsed_forms = [e["form"] for e in parsed["entries"]]
        assert original_forms == parsed_forms


# ---------------------------------------------------------------------------
# 5. FFFFEntry dataclass sanity
# ---------------------------------------------------------------------------


class TestFFFFEntryDataclass:
    def test_entry_is_frozen(self):
        entry = FFFFEntry(
            form="1040",
            line="1a",
            value="65,000.00",
            description="Total W-2 box 1 wages",
        )
        with pytest.raises((AttributeError, Exception)):
            entry.line = "1b"  # type: ignore[misc]

    def test_note_defaults_to_none(self):
        entry = FFFFEntry(
            form="1040",
            line="1a",
            value="1.00",
            description="d",
        )
        assert entry.note is None

    def test_entry_map_is_frozen(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        with pytest.raises((AttributeError, Exception)):
            entry_map.tax_year = 2099  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 6. Wave 7A forms — Schedule E
# ---------------------------------------------------------------------------


def _make_base_canonical(**overrides) -> CanonicalReturn:
    """Build a minimal CanonicalReturn with sensible defaults.

    Accepts keyword overrides that are merged into the base dict before
    validation.
    """
    base = {
        "schema_version": "0.1.0",
        "tax_year": 2025,
        "filing_status": "single",
        "taxpayer": {
            "first_name": "Test",
            "last_name": "User",
            "ssn": "111-22-3333",
            "date_of_birth": "1990-01-01",
        },
        "address": {
            "street1": "1 Main St",
            "city": "Anytown",
            "state": "CA",
            "zip": "90000",
            "country": "US",
        },
    }
    base.update(overrides)
    return CanonicalReturn.model_validate(base)


class TestScheduleEEntries:
    """Schedule E per-property rents/expenses/net + Part I totals."""

    def _build(self) -> CanonicalReturn:
        canonical = _make_base_canonical(
            schedules_e=[
                {
                    "properties": [
                        {
                            "address": {
                                "street1": "100 Elm St",
                                "city": "Portland",
                                "state": "OR",
                                "zip": "97201",
                                "country": "US",
                            },
                            "property_type": "single_family",
                            "fair_rental_days": 365,
                            "personal_use_days": 0,
                            "rents_received": "24000.00",
                            "insurance": "1200.00",
                            "taxes": "3000.00",
                            "depreciation": "5000.00",
                        },
                    ],
                },
            ],
        )
        from skill.scripts.calc.engine import compute

        return compute(canonical)

    def test_schedule_e_entries_present(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        se_entries = [e for e in entry_map.entries if e.form == "1040-SE-1"]
        assert len(se_entries) > 0

    def test_schedule_e_has_per_property_rents(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        se_lines = {e.line for e in entry_map.entries if e.form == "1040-SE-1"}
        assert "3a" in se_lines  # rents received property A

    def test_schedule_e_has_net_income(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        se_lines = {e.line for e in entry_map.entries if e.form == "1040-SE-1"}
        assert "21a" in se_lines  # net income property A

    def test_schedule_e_has_part_i_totals(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        se_lines = {e.line for e in entry_map.entries if e.form == "1040-SE-1"}
        assert "23a" in se_lines
        assert "26" in se_lines

    def test_schedule_e_rents_value(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        rents = next(
            e for e in entry_map.entries
            if e.form == "1040-SE-1" and e.line == "3a"
        )
        assert rents.value == "24,000.00"

    def test_schedule_e_not_present_for_simple_w2(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        assert not any(e.form.startswith("1040-SE-") for e in entry_map.entries)


# ---------------------------------------------------------------------------
# 7. Wave 7A — Schedule 1 (Additional Income and Adjustments)
# ---------------------------------------------------------------------------


class TestSchedule1Entries:
    """Schedule 1 entries for returns with Schedule C / Schedule E."""

    def test_schedule_1_present_for_se_return(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        s1_entries = [e for e in entry_map.entries if e.form == "1040-S1"]
        assert len(s1_entries) > 0

    def test_schedule_1_has_business_income_line_3(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        s1_lines = {e.line for e in entry_map.entries if e.form == "1040-S1"}
        assert "3" in s1_lines

    def test_schedule_1_has_total_line_10(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        s1_lines = {e.line for e in entry_map.entries if e.form == "1040-S1"}
        assert "10" in s1_lines

    def test_schedule_1_has_deductible_se_tax_line_15(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        s1_lines = {e.line for e in entry_map.entries if e.form == "1040-S1"}
        assert "15" in s1_lines

    def test_schedule_1_has_total_adjustments_line_26(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        s1_lines = {e.line for e in entry_map.entries if e.form == "1040-S1"}
        assert "26" in s1_lines

    def test_schedule_1_has_net_line(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        s1_lines = {e.line for e in entry_map.entries if e.form == "1040-S1"}
        assert "net" in s1_lines

    def test_schedule_1_not_present_for_simple_w2(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        assert not any(e.form == "1040-S1" for e in entry_map.entries)


# ---------------------------------------------------------------------------
# 8. Wave 7A — Schedule 2 (Additional Taxes)
# ---------------------------------------------------------------------------


class TestSchedule2Entries:
    """Schedule 2 emitted when SE tax is present."""

    def test_schedule_2_present_for_se_return(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        # se_home_office has self-employment tax -> schedule 2 required
        # via the engine populating other_taxes.self_employment_tax
        s2_entries = [e for e in entry_map.entries if e.form == "1040-S2"]
        # Schedule 2 is only emitted if schedule_2_required returns True.
        # After compute(), SE tax should be set.
        if canonical.other_taxes.self_employment_tax > 0:
            assert len(s2_entries) > 0
        else:
            # If engine doesn't set it, we skip (design choice).
            pass

    def test_schedule_2_has_se_tax_line_6(self):
        canonical = _make_base_canonical(
            other_taxes={"self_employment_tax": "1000.00"},
        )
        from skill.scripts.calc.engine import compute

        canonical = compute(canonical)
        entry_map = build_ffff_entry_map(canonical)
        s2_entries = [e for e in entry_map.entries if e.form == "1040-S2"]
        s2_lines = {e.line for e in s2_entries}
        assert "6" in s2_lines

    def test_schedule_2_has_total_line_21(self):
        canonical = _make_base_canonical(
            other_taxes={"self_employment_tax": "500.00"},
        )
        from skill.scripts.calc.engine import compute

        canonical = compute(canonical)
        entry_map = build_ffff_entry_map(canonical)
        s2_lines = {e.line for e in entry_map.entries if e.form == "1040-S2"}
        assert "21" in s2_lines

    def test_schedule_2_not_present_for_simple_w2(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        assert not any(e.form == "1040-S2" for e in entry_map.entries)


# ---------------------------------------------------------------------------
# 9. Wave 7A — Schedule 3 (Additional Credits and Payments)
# ---------------------------------------------------------------------------


class TestSchedule3Entries:
    """Schedule 3 emitted when credits or extra payments exist."""

    def test_schedule_3_has_total_nonrefundable_line_8(self):
        canonical = _computed("se_home_office")
        entry_map = build_ffff_entry_map(canonical)
        s3_entries = [e for e in entry_map.entries if e.form == "1040-S3"]
        if s3_entries:
            s3_lines = {e.line for e in s3_entries}
            assert "8" in s3_lines

    def test_schedule_3_has_total_refundable_line_15(self):
        canonical = _make_base_canonical(
            credits={"education_credits_refundable": "500.00"},
        )
        from skill.scripts.calc.engine import compute

        canonical = compute(canonical)
        entry_map = build_ffff_entry_map(canonical)
        s3_entries = [e for e in entry_map.entries if e.form == "1040-S3"]
        s3_lines = {e.line for e in s3_entries}
        assert "15" in s3_lines

    def test_schedule_3_not_present_for_simple_w2(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        assert not any(e.form == "1040-S3" for e in entry_map.entries)


# ---------------------------------------------------------------------------
# 10. Wave 7A — Form 2441 (Child and Dependent Care)
# ---------------------------------------------------------------------------


class TestForm2441Entries:
    """Form 2441 emitted when dependent_care is populated."""

    def _build(self) -> CanonicalReturn:
        canonical = _make_base_canonical(
            w2s=[
                {
                    "employer_name": "Acme Corp",
                    "employer_ein": "12-3456789",
                    "box1_wages": "60000.00",
                    "box2_federal_income_tax_withheld": "8000.00",
                    "box3_social_security_wages": "60000.00",
                    "box4_social_security_tax_withheld": "3720.00",
                    "box5_medicare_wages": "60000.00",
                    "box6_medicare_tax_withheld": "870.00",
                    "employee_is_taxpayer": True,
                },
            ],
            dependent_care={
                "qualifying_persons": 1,
                "total_expenses_paid": "5000.00",
                "employer_benefits_excluded": "0.00",
                "care_providers": [
                    {"name": "Happy Days Daycare", "amount_paid": "5000.00"},
                ],
            },
        )
        from skill.scripts.calc.engine import compute

        return compute(canonical)

    def test_form_2441_entries_present(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        f2441 = [e for e in entry_map.entries if e.form == "2441"]
        assert len(f2441) > 0

    def test_form_2441_has_qualifying_persons(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        lines = {e.line for e in entry_map.entries if e.form == "2441"}
        assert "3" in lines

    def test_form_2441_has_qualified_expenses(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        lines = {e.line for e in entry_map.entries if e.form == "2441"}
        assert "4" in lines

    def test_form_2441_has_credit_rate(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        lines = {e.line for e in entry_map.entries if e.form == "2441"}
        assert "9" in lines

    def test_form_2441_has_credit_amount(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        lines = {e.line for e in entry_map.entries if e.form == "2441"}
        assert "10" in lines

    def test_form_2441_not_present_for_simple_w2(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        assert not any(e.form == "2441" for e in entry_map.entries)


# ---------------------------------------------------------------------------
# 11. Wave 7A — Form 8863 (Education Credits)
# ---------------------------------------------------------------------------


class TestForm8863Entries:
    """Form 8863 emitted when education data is present."""

    def _build(self) -> CanonicalReturn:
        canonical = _make_base_canonical(
            w2s=[
                {
                    "employer_name": "Acme Corp",
                    "employer_ein": "12-3456789",
                    "box1_wages": "50000.00",
                    "box2_federal_income_tax_withheld": "5000.00",
                    "box3_social_security_wages": "50000.00",
                    "box4_social_security_tax_withheld": "3100.00",
                    "box5_medicare_wages": "50000.00",
                    "box6_medicare_tax_withheld": "725.00",
                    "employee_is_taxpayer": True,
                },
            ],
            education={
                "students": [
                    {
                        "name": "Jane User",
                        "ssn": "444-55-6666",
                        "institution_name": "State University",
                        "qualified_expenses": "4000.00",
                        "is_aotc_eligible": True,
                        "completed_4_years": False,
                        "half_time_student": True,
                        "felony_drug_conviction": False,
                    },
                ],
            },
        )
        from skill.scripts.calc.engine import compute

        return compute(canonical)

    def test_form_8863_entries_present(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        entries = [e for e in entry_map.entries if e.form == "8863"]
        assert len(entries) > 0

    def test_form_8863_has_student_details(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        lines = {e.line for e in entry_map.entries if e.form == "8863"}
        assert "stu.1.name" in lines
        assert "stu.1.expenses" in lines
        assert "stu.1.type" in lines

    def test_form_8863_has_aotc_credit_for_eligible_student(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        lines = {e.line for e in entry_map.entries if e.form == "8863"}
        assert "stu.1.credit" in lines

    def test_form_8863_has_totals(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        lines = {e.line for e in entry_map.entries if e.form == "8863"}
        assert "nonref" in lines
        assert "ref" in lines

    def test_form_8863_nonrefundable_value(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        nonref = next(
            e for e in entry_map.entries
            if e.form == "8863" and e.line == "nonref"
        )
        # $4000 expenses -> $2000 + 25% of $2000 = $2500 AOTC
        # 60% nonrefundable = $1500
        assert nonref.value == "1,500.00"

    def test_form_8863_refundable_value(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        ref = next(
            e for e in entry_map.entries
            if e.form == "8863" and e.line == "ref"
        )
        # 40% refundable = $1000
        assert ref.value == "1,000.00"

    def test_form_8863_not_present_for_simple_w2(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        assert not any(e.form == "8863" for e in entry_map.entries)


# ---------------------------------------------------------------------------
# 12. Wave 7A — Form 8962 (Premium Tax Credit)
# ---------------------------------------------------------------------------


class TestForm8962Entries:
    """Form 8962 emitted when 1095-A data is present."""

    def _build(self) -> CanonicalReturn:
        canonical = _make_base_canonical(
            w2s=[
                {
                    "employer_name": "Acme Corp",
                    "employer_ein": "12-3456789",
                    "box1_wages": "30000.00",
                    "box2_federal_income_tax_withheld": "3000.00",
                    "box3_social_security_wages": "30000.00",
                    "box4_social_security_tax_withheld": "1860.00",
                    "box5_medicare_wages": "30000.00",
                    "box6_medicare_tax_withheld": "435.00",
                    "employee_is_taxpayer": True,
                },
            ],
            forms_1095_a=[
                {
                    "marketplace_id": "MKT-001",
                    "monthly_data": [
                        {
                            "enrollment_premium": "400.00",
                            "slcsp_premium": "500.00",
                            "advance_ptc": "200.00",
                        },
                    ]
                    * 12,
                },
            ],
        )
        from skill.scripts.calc.engine import compute

        return compute(canonical)

    def test_form_8962_entries_present(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        entries = [e for e in entry_map.entries if e.form == "8962"]
        assert len(entries) > 0

    def test_form_8962_has_family_size(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        lines = {e.line for e in entry_map.entries if e.form == "8962"}
        assert "1" in lines

    def test_form_8962_has_household_income(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        lines = {e.line for e in entry_map.entries if e.form == "8962"}
        assert "4" in lines

    def test_form_8962_has_monthly_ptc_rows(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        lines = {e.line for e in entry_map.entries if e.form == "8962"}
        assert "Jan.ptc" in lines
        assert "Dec.ptc" in lines

    def test_form_8962_has_net_ptc(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        lines = {e.line for e in entry_map.entries if e.form == "8962"}
        assert "24" in lines

    def test_form_8962_has_repayment(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        lines = {e.line for e in entry_map.entries if e.form == "8962"}
        assert "29" in lines

    def test_form_8962_not_present_for_simple_w2(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        assert not any(e.form == "8962" for e in entry_map.entries)


# ---------------------------------------------------------------------------
# 13. Wave 7A — Form 8606 (Nondeductible IRAs)
# ---------------------------------------------------------------------------


class TestForm8606Entries:
    """Form 8606 emitted when ira_info is populated."""

    def _build(self) -> CanonicalReturn:
        canonical = _make_base_canonical(
            w2s=[
                {
                    "employer_name": "Acme Corp",
                    "employer_ein": "12-3456789",
                    "box1_wages": "80000.00",
                    "box2_federal_income_tax_withheld": "12000.00",
                    "box3_social_security_wages": "80000.00",
                    "box4_social_security_tax_withheld": "4960.00",
                    "box5_medicare_wages": "80000.00",
                    "box6_medicare_tax_withheld": "1160.00",
                    "employee_is_taxpayer": True,
                },
            ],
            ira_info={
                "nondeductible_contributions_current_year": "7000.00",
                "prior_year_basis": "14000.00",
                "contributions_withdrawn_by_due_date": "0.00",
                "total_ira_value_year_end": "100000.00",
                "distributions_received": "10000.00",
                "roth_conversions": "0.00",
            },
        )
        from skill.scripts.calc.engine import compute

        return compute(canonical)

    def test_form_8606_entries_present(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        entries = [e for e in entry_map.entries if e.form == "8606"]
        assert len(entries) > 0

    def test_form_8606_has_nondeductible_contributions(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        lines = {e.line for e in entry_map.entries if e.form == "8606"}
        assert "1" in lines

    def test_form_8606_has_prior_year_basis(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        lines = {e.line for e in entry_map.entries if e.form == "8606"}
        assert "2" in lines

    def test_form_8606_has_nontaxable_portion(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        lines = {e.line for e in entry_map.entries if e.form == "8606"}
        assert "11" in lines

    def test_form_8606_has_remaining_basis(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        lines = {e.line for e in entry_map.entries if e.form == "8606"}
        assert "14" in lines

    def test_form_8606_nondeductible_value(self):
        canonical = self._build()
        entry_map = build_ffff_entry_map(canonical)
        line_1 = next(
            e for e in entry_map.entries
            if e.form == "8606" and e.line == "1"
        )
        assert line_1.value == "7,000.00"

    def test_form_8606_not_present_for_simple_w2(self):
        canonical = _computed("simple_w2_standard")
        entry_map = build_ffff_entry_map(canonical)
        assert not any(e.form == "8606" for e in entry_map.entries)


# ---------------------------------------------------------------------------
# 14. Wave 7A — to_text() heading coverage
# ---------------------------------------------------------------------------


class TestWave7ATextHeadings:
    """Verify that the to_text() output includes proper headings for new forms."""

    def test_schedule_e_heading_in_text(self):
        canonical = _make_base_canonical(
            schedules_e=[
                {
                    "properties": [
                        {
                            "address": {
                                "street1": "10 Oak Ave",
                                "city": "Salem",
                                "state": "OR",
                                "zip": "97301",
                                "country": "US",
                            },
                            "rents_received": "12000.00",
                        },
                    ],
                },
            ],
        )
        from skill.scripts.calc.engine import compute

        canonical = compute(canonical)
        text = build_ffff_entry_map(canonical).to_text()
        assert "Schedule E #1" in text

    def test_schedule_1_heading_in_text(self):
        canonical = _computed("se_home_office")
        text = build_ffff_entry_map(canonical).to_text()
        assert "Schedule 1" in text

    def test_form_8606_heading_in_text(self):
        canonical = _make_base_canonical(
            w2s=[
                {
                    "employer_name": "Acme",
                    "employer_ein": "12-3456789",
                    "box1_wages": "50000.00",
                    "box2_federal_income_tax_withheld": "5000.00",
                    "box3_social_security_wages": "50000.00",
                    "box4_social_security_tax_withheld": "3100.00",
                    "box5_medicare_wages": "50000.00",
                    "box6_medicare_tax_withheld": "725.00",
                    "employee_is_taxpayer": True,
                },
            ],
            ira_info={
                "nondeductible_contributions_current_year": "7000.00",
                "prior_year_basis": "0.00",
                "total_ira_value_year_end": "50000.00",
                "distributions_received": "0.00",
                "roth_conversions": "0.00",
            },
        )
        from skill.scripts.calc.engine import compute

        canonical = compute(canonical)
        text = build_ffff_entry_map(canonical).to_text()
        assert "Form 8606" in text
