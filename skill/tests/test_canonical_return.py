"""Canonical return schema + Pydantic model tests.

Tests cover:
- Minimal valid return round-trips through the model
- Filing-status + spouse validation
- Itemize flag requires itemized block
- Dependent qualifying-kind validation
- Generated schema matches committed schema (no drift after model edits)
- jsonschema validates a known-good return against the committed schema
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import jsonschema
import pytest
from pydantic import ValidationError

from skill.scripts.generate_schema import generate as generate_schema
from skill.scripts.models import (
    Address,
    AdjustmentsToIncome,
    CanonicalReturn,
    Credits,
    Dependent,
    DependentRelationship,
    FilingStatus,
    ItemizedDeductions,
    OtherTaxes,
    Payments,
    Person,
    PriorYearCarryforwards,
    ResidencyStatus,
    SCHEMA_VERSION,
    ScheduleC,
    ScheduleCExpenses,
    StateReturn,
    W2,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def minimal_single_return() -> CanonicalReturn:
    """Simplest possible valid return: single filer, one W-2, standard deduction."""
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.SINGLE,
        taxpayer=Person(
            first_name="Alex",
            last_name="Doe",
            ssn="123-45-6789",
            date_of_birth=dt.date(1990, 1, 15),
        ),
        address=Address(
            street1="1 Test Lane",
            city="Springfield",
            state="IL",
            zip="62701",
        ),
        w2s=[
            W2(
                employer_name="Acme Corp",
                employer_ein="12-3456789",
                box1_wages=Decimal("65000.00"),
                box2_federal_income_tax_withheld=Decimal("7500.00"),
                box3_social_security_wages=Decimal("65000.00"),
                box4_social_security_tax_withheld=Decimal("4030.00"),
                box5_medicare_wages=Decimal("65000.00"),
                box6_medicare_tax_withheld=Decimal("942.50"),
            )
        ],
    )


@pytest.fixture
def minimal_mfj_return() -> CanonicalReturn:
    """Married filing jointly, both spouses have W-2s, one dependent child."""
    return CanonicalReturn(
        tax_year=2025,
        filing_status=FilingStatus.MFJ,
        taxpayer=Person(
            first_name="Jamie",
            last_name="Smith",
            ssn="111-22-3333",
            date_of_birth=dt.date(1985, 6, 1),
        ),
        spouse=Person(
            first_name="Pat",
            last_name="Smith",
            ssn="444-55-6666",
            date_of_birth=dt.date(1986, 8, 20),
        ),
        address=Address(
            street1="123 Maple St",
            city="Burlington",
            state="VT",
            zip="05401",
        ),
        dependents=[
            Dependent(
                person=Person(
                    first_name="Taylor",
                    last_name="Smith",
                    ssn="777-88-9999",
                    date_of_birth=dt.date(2015, 3, 10),
                ),
                relationship=DependentRelationship.SON,
                months_lived_with_taxpayer=12,
                is_qualifying_child=True,
                is_qualifying_relative=False,
            )
        ],
        w2s=[
            W2(
                employer_name="Employer A",
                box1_wages=Decimal("80000"),
                employee_is_taxpayer=True,
            ),
            W2(
                employer_name="Employer B",
                box1_wages=Decimal("55000"),
                employee_is_taxpayer=False,
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Schema version + basic round-trip
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_schema_version_constant(self):
        assert SCHEMA_VERSION == "0.1.0"

    def test_minimal_single_serializes_and_reloads(self, minimal_single_return):
        data = minimal_single_return.model_dump(mode="json")
        reloaded = CanonicalReturn.model_validate(data)
        assert reloaded == minimal_single_return

    def test_mfj_with_dependent_round_trip(self, minimal_mfj_return):
        data = minimal_mfj_return.model_dump(mode="json")
        reloaded = CanonicalReturn.model_validate(data)
        assert reloaded.dependents[0].person.first_name == "Taylor"
        assert reloaded.spouse is not None
        assert reloaded.spouse.first_name == "Pat"

    def test_default_field_factories_create_empty_containers(self, minimal_single_return):
        r = minimal_single_return
        assert r.dependents == []
        assert r.forms_1099_int == []
        assert r.schedules_c == []
        assert r.adjustments == AdjustmentsToIncome()
        assert r.credits == Credits()
        assert r.other_taxes == OtherTaxes()
        assert r.payments == Payments()
        assert r.carryforwards == PriorYearCarryforwards()
        assert r.computed.total_income is None


# ---------------------------------------------------------------------------
# Validation rules
# ---------------------------------------------------------------------------


class TestValidation:
    def test_mfj_requires_spouse(self):
        with pytest.raises(ValidationError, match="requires spouse"):
            CanonicalReturn(
                tax_year=2025,
                filing_status=FilingStatus.MFJ,
                taxpayer=Person(
                    first_name="A",
                    last_name="B",
                    ssn="111-22-3333",
                    date_of_birth=dt.date(1990, 1, 1),
                ),
                address=Address(street1="1 A", city="B", state="CA", zip="90001"),
            )

    def test_single_rejects_spouse(self):
        with pytest.raises(ValidationError, match="should not have spouse"):
            CanonicalReturn(
                tax_year=2025,
                filing_status=FilingStatus.SINGLE,
                taxpayer=Person(
                    first_name="A",
                    last_name="B",
                    ssn="111-22-3333",
                    date_of_birth=dt.date(1990, 1, 1),
                ),
                spouse=Person(
                    first_name="C",
                    last_name="D",
                    ssn="444-55-6666",
                    date_of_birth=dt.date(1990, 1, 1),
                ),
                address=Address(street1="1 A", city="B", state="CA", zip="90001"),
            )

    def test_itemize_flag_requires_itemized_block(self):
        with pytest.raises(ValidationError, match="requires itemized block"):
            CanonicalReturn(
                tax_year=2025,
                filing_status=FilingStatus.SINGLE,
                taxpayer=Person(
                    first_name="A",
                    last_name="B",
                    ssn="111-22-3333",
                    date_of_birth=dt.date(1990, 1, 1),
                ),
                address=Address(street1="1 A", city="B", state="CA", zip="90001"),
                itemize_deductions=True,
                itemized=None,
            )

    def test_itemized_block_is_allowed(self):
        r = CanonicalReturn(
            tax_year=2025,
            filing_status=FilingStatus.SINGLE,
            taxpayer=Person(
                first_name="A",
                last_name="B",
                ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
            ),
            address=Address(street1="1 A", city="B", state="CA", zip="90001"),
            itemize_deductions=True,
            itemized=ItemizedDeductions(
                state_and_local_income_tax=Decimal("10000"),
                real_estate_tax=Decimal("5000"),
                home_mortgage_interest=Decimal("12000"),
            ),
        )
        assert r.itemized is not None
        assert r.itemized.state_and_local_income_tax == Decimal("10000")

    def test_tax_year_before_2024_rejected(self):
        with pytest.raises(ValidationError):
            CanonicalReturn(
                tax_year=2023,
                filing_status=FilingStatus.SINGLE,
                taxpayer=Person(
                    first_name="A",
                    last_name="B",
                    ssn="111-22-3333",
                    date_of_birth=dt.date(1990, 1, 1),
                ),
                address=Address(street1="1 A", city="B", state="CA", zip="90001"),
            )

    def test_ssn_format_enforced(self):
        with pytest.raises(ValidationError):
            Person(
                first_name="A",
                last_name="B",
                ssn="not-a-ssn",
                date_of_birth=dt.date(1990, 1, 1),
            )

    def test_ssn_accepts_both_formats(self):
        with_hyphens = Person(
            first_name="A", last_name="B", ssn="123-45-6789", date_of_birth=dt.date(1990, 1, 1)
        )
        without = Person(
            first_name="A", last_name="B", ssn="123456789", date_of_birth=dt.date(1990, 1, 1)
        )
        assert with_hyphens.ssn == "123-45-6789"
        assert without.ssn == "123456789"

    def test_state_code_must_be_uppercase_two_letters(self):
        with pytest.raises(ValidationError):
            Address(street1="1 A", city="B", state="California", zip="90001")
        with pytest.raises(ValidationError):
            Address(street1="1 A", city="B", state="ca", zip="90001")
        ok = Address(street1="1 A", city="B", state="CA", zip="90001")
        assert ok.state == "CA"

    def test_zip_format_enforced(self):
        with pytest.raises(ValidationError):
            Address(street1="1 A", city="B", state="CA", zip="9001")
        ok5 = Address(street1="1 A", city="B", state="CA", zip="90001")
        ok9 = Address(street1="1 A", city="B", state="CA", zip="90001-1234")
        assert ok5.zip == "90001"
        assert ok9.zip == "90001-1234"

    def test_dependent_cannot_be_both_child_and_relative(self):
        with pytest.raises(ValidationError, match="cannot be both"):
            Dependent(
                person=Person(
                    first_name="X",
                    last_name="Y",
                    ssn="111-22-3333",
                    date_of_birth=dt.date(2010, 1, 1),
                ),
                relationship=DependentRelationship.SON,
                months_lived_with_taxpayer=12,
                is_qualifying_child=True,
                is_qualifying_relative=True,
            )

    def test_dependent_must_be_one_qualifying_kind(self):
        with pytest.raises(ValidationError, match="must be either"):
            Dependent(
                person=Person(
                    first_name="X",
                    last_name="Y",
                    ssn="111-22-3333",
                    date_of_birth=dt.date(2010, 1, 1),
                ),
                relationship=DependentRelationship.SON,
                months_lived_with_taxpayer=12,
                is_qualifying_child=False,
                is_qualifying_relative=False,
            )

    def test_extra_fields_are_forbidden(self):
        """Strict models reject unknown fields so typos surface loudly."""
        with pytest.raises(ValidationError):
            Person(
                first_name="A",
                last_name="B",
                ssn="111-22-3333",
                date_of_birth=dt.date(1990, 1, 1),
                unknown_field="oops",  # type: ignore[call-arg]
            )


# ---------------------------------------------------------------------------
# Schedule C and Schedule E can round-trip
# ---------------------------------------------------------------------------


class TestScheduleCAndE:
    def test_schedule_c_total_expenses_preserved(self, minimal_single_return):
        sched_c = ScheduleC(
            business_name="Freelance Co",
            principal_business_or_profession="consulting",
            line1_gross_receipts=Decimal("50000"),
            expenses=ScheduleCExpenses(
                line8_advertising=Decimal("500"),
                line22_supplies=Decimal("1200"),
                line25_utilities=Decimal("600"),
            ),
        )
        minimal_single_return.schedules_c.append(sched_c)
        data = minimal_single_return.model_dump(mode="json")
        reloaded = CanonicalReturn.model_validate(data)
        assert reloaded.schedules_c[0].expenses.line8_advertising == Decimal("500")
        assert reloaded.schedules_c[0].expenses.line22_supplies == Decimal("1200")

    def test_state_return_payload_preserved(self, minimal_single_return):
        minimal_single_return.state_returns.append(
            StateReturn(
                state="IL",
                residency=ResidencyStatus.RESIDENT,
                days_in_state=365,
                state_specific={
                    "state_total_tax": 2775,
                    "exemption_credit": 2775,
                },
            )
        )
        data = minimal_single_return.model_dump(mode="json")
        reloaded = CanonicalReturn.model_validate(data)
        assert reloaded.state_returns[0].state == "IL"
        assert reloaded.state_returns[0].state_specific["state_total_tax"] == 2775
        assert reloaded.state_returns[0].state_specific["exemption_credit"] == 2775


# ---------------------------------------------------------------------------
# JSON schema generation and drift detection
# ---------------------------------------------------------------------------


class TestSchemaFile:
    def test_committed_schema_matches_generated(self, schemas_dir):
        """If this fails, someone edited models.py without running generate_schema.py."""
        committed = json.loads((schemas_dir / "return.schema.json").read_text())
        generated = generate_schema()
        generated["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        generated["$id"] = "https://tax-prep-skill/schemas/return.schema.json"
        generated["title"] = "CanonicalReturn"
        # Compare normalized to avoid key-order noise
        assert json.dumps(committed, sort_keys=True) == json.dumps(generated, sort_keys=True), (
            "Committed schemas/return.schema.json is out of date. "
            "Run `.venv/bin/python -m skill.scripts.generate_schema` and commit the result."
        )

    def test_jsonschema_validates_minimal_return(self, schemas_dir, minimal_single_return):
        schema = json.loads((schemas_dir / "return.schema.json").read_text())
        data = minimal_single_return.model_dump(mode="json")
        jsonschema.validate(instance=data, schema=schema)

    def test_jsonschema_rejects_missing_required_field(self, schemas_dir):
        schema = json.loads((schemas_dir / "return.schema.json").read_text())
        bad = {
            "schema_version": "0.1.0",
            "tax_year": 2025,
            "filing_status": "single",
            # missing taxpayer
            "address": {
                "street1": "1 A",
                "city": "B",
                "state": "CA",
                "zip": "90001",
                "country": "US",
            },
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=bad, schema=schema)
