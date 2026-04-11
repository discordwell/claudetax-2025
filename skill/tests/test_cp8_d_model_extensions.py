"""Tests for the CP8-D canonical model extensions.

Locks the three new model fields wave 5 will rely on:

  1. ``Address.county`` — for county-level local tax routing (MD is
     the current consumer; wave-5 states may add more).
  2. W-2 ``box14_qualified_tips_obbba`` / ``box14_qualified_overtime_obbba``
     — structured OBBBA §224/§225 employer-attested inputs for the
     Schedule 1-A patch (replaces caller-populated adjustment fields
     for ingested W-2s).
  3. ``CanonicalReturn.has_foreign_financial_account_over_10k``,
     ``has_foreign_trust_transaction``, and ``foreign_account_countries``
     — Schedule B Part III foreign-account flags wired into the
     Schedule B renderer and required-filing threshold.

These are additive defaults so existing fixtures and tests are bit-for-
bit unchanged. The schema drift test in ``test_canonical_return.py``
covers the JSON schema regeneration.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from skill.scripts.models import (
    Address,
    CanonicalReturn,
    FilingStatus,
    Form1099INT,
    Person,
    W2,
)
from skill.scripts.output.schedule_b import (
    compute_schedule_b_fields,
    schedule_b_required,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _person() -> Person:
    return Person(
        first_name="Alex",
        last_name="Doe",
        ssn="111-22-3333",
        date_of_birth=dt.date(1985, 1, 1),
    )


def _return_with_flags(
    *,
    county: str | None = None,
    has_fbar: bool = False,
    has_foreign_trust: bool = False,
    countries: tuple[str, ...] = (),
    box14_tips: Decimal = Decimal("0"),
    box14_overtime: Decimal = Decimal("0"),
) -> CanonicalReturn:
    addr_kwargs: dict = dict(
        street1="1 Test Lane", city="Springfield", state="IL", zip="62701"
    )
    if county is not None:
        addr_kwargs["county"] = county
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=_person(),
        address=Address(**addr_kwargs),
        w2s=[
            W2(
                employer_name="Acme",
                box1_wages=Decimal("65000.00"),
                box14_qualified_tips_obbba=box14_tips,
                box14_qualified_overtime_obbba=box14_overtime,
            )
        ],
        has_foreign_financial_account_over_10k=has_fbar,
        has_foreign_trust_transaction=has_foreign_trust,
        foreign_account_countries=list(countries),
    )


# ---------------------------------------------------------------------------
# Address.county
# ---------------------------------------------------------------------------


class TestAddressCounty:
    def test_county_defaults_to_none(self):
        addr = Address(street1="1 A", city="B", state="MD", zip="21201")
        assert addr.county is None

    def test_county_accepts_string(self):
        addr = Address(
            street1="1 A", city="B", state="MD", zip="21201",
            county="baltimore city",
        )
        assert addr.county == "baltimore city"

    def test_county_round_trips_json(self):
        addr = Address(
            street1="1 A", city="B", state="MD", zip="21201",
            county="anne arundel",
        )
        data = addr.model_dump(mode="json")
        assert data["county"] == "anne arundel"
        reloaded = Address.model_validate(data)
        assert reloaded.county == "anne arundel"

    def test_existing_fixtures_without_county_still_valid(self):
        """Existing goldens and test fixtures don't set county. They
        must still validate."""
        addr = Address(street1="1 A", city="B", state="MD", zip="21201")
        assert addr.model_dump(mode="json")["county"] is None


# ---------------------------------------------------------------------------
# W2 box14 OBBBA fields
# ---------------------------------------------------------------------------


class TestW2Box14OBBBA:
    def test_box14_defaults_to_zero(self):
        w2 = W2(employer_name="X", box1_wages=Decimal("60000"))
        assert w2.box14_qualified_tips_obbba == Decimal("0")
        assert w2.box14_qualified_overtime_obbba == Decimal("0")

    def test_box14_accepts_employer_attested_tips(self):
        w2 = W2(
            employer_name="Diner",
            box1_wages=Decimal("60000"),
            box14_qualified_tips_obbba=Decimal("5000.00"),
        )
        assert w2.box14_qualified_tips_obbba == Decimal("5000.00")

    def test_box14_accepts_employer_attested_overtime(self):
        w2 = W2(
            employer_name="Factory",
            box1_wages=Decimal("75000"),
            box14_qualified_overtime_obbba=Decimal("8000.00"),
        )
        assert w2.box14_qualified_overtime_obbba == Decimal("8000.00")

    def test_box14_both_independent_of_box7_tips(self):
        """box14 OBBBA qualifying tips are independent of box7
        Social Security tips — one W-2 can have both."""
        w2 = W2(
            employer_name="Diner",
            box1_wages=Decimal("60000"),
            box7_social_security_tips=Decimal("8000"),
            box14_qualified_tips_obbba=Decimal("5000"),
        )
        assert w2.box7_social_security_tips == Decimal("8000")
        assert w2.box14_qualified_tips_obbba == Decimal("5000")


# ---------------------------------------------------------------------------
# CanonicalReturn foreign-accounts flags
# ---------------------------------------------------------------------------


class TestForeignAccountsFields:
    def test_defaults_to_no_foreign_accounts(self):
        r = _return_with_flags()
        assert r.has_foreign_financial_account_over_10k is False
        assert r.has_foreign_trust_transaction is False
        assert r.foreign_account_countries == []

    def test_schedule_b_not_required_under_thresholds_with_no_flags(self):
        r = _return_with_flags()
        assert schedule_b_required(r) is False

    def test_foreign_account_flag_triggers_schedule_b_required(self):
        """A taxpayer with under-$1,500 interest but a foreign account
        still must file Schedule B."""
        r = _return_with_flags(has_fbar=True, countries=("FR",))
        r = r.model_copy(
            update={
                "forms_1099_int": [
                    Form1099INT(
                        payer_name="Big Bank",
                        box1_interest_income=Decimal("500"),
                    )
                ]
            }
        )
        assert schedule_b_required(r) is True

    def test_foreign_trust_flag_triggers_schedule_b_required(self):
        r = _return_with_flags(has_foreign_trust=True)
        assert schedule_b_required(r) is True

    def test_foreign_country_flows_to_line_7b(self):
        """The first country in foreign_account_countries lands on
        Schedule B line 7b (the renderer's Part III block)."""
        r = _return_with_flags(
            has_fbar=True, countries=("FR", "CH", "SG"),
        )
        fields = compute_schedule_b_fields(r)
        assert fields.part_iii_line_7a_foreign_account is True
        assert fields.part_iii_line_7a_fincen114_required is True
        # The renderer shows only the first country; multi-country
        # enumeration is handled by FinCEN 114 itself.
        assert fields.part_iii_line_7b_fincen114_country == "FR"

    def test_empty_countries_list_renders_dash(self):
        """has_fbar=True without any countries produces an empty
        country string, which the PDF scaffold shows as a dash."""
        r = _return_with_flags(has_fbar=True, countries=())
        fields = compute_schedule_b_fields(r)
        assert fields.part_iii_line_7a_foreign_account is True
        assert fields.part_iii_line_7b_fincen114_country == ""

    def test_model_round_trips_json_with_flags(self):
        r = _return_with_flags(
            has_fbar=True, has_foreign_trust=True, countries=("JP",),
        )
        data = r.model_dump(mode="json")
        assert data["has_foreign_financial_account_over_10k"] is True
        assert data["has_foreign_trust_transaction"] is True
        assert data["foreign_account_countries"] == ["JP"]
        reloaded = CanonicalReturn.model_validate(data)
        assert reloaded.has_foreign_financial_account_over_10k is True
        assert reloaded.foreign_account_countries == ["JP"]
