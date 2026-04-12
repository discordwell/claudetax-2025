"""Canonical return Pydantic models — the data contract for the entire skill.

Every module (calc, ingest, output, states) reads from and writes to CanonicalReturn.
Fields are aligned to 1040-series line numbers where possible. Where a calc module
needs a field that isn't here yet, ADD IT — do not work around the model.

Design notes:
- Strict on the top-level shape and on common fields (W-2, Schedule C, standard
  deduction, CTC).
- Loose (arbitrary dict) on infrequently-used fields so modules can land in fan-out
  without blocking on schema completeness. Tighten up over time.
- SSN/EIN fields are plain strings with format validation; the canonical return
  lives in the user's own directory outside this repo.
- Schema version tracked at the root so future migrations are detectable.
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

SCHEMA_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# Base config and reused types
# ---------------------------------------------------------------------------


class _StrictModel(BaseModel):
    """Base for every canonical-return model. Strict mode, immutable."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=False,  # allow in-place updates during interview
        str_strip_whitespace=True,
    )


Money = Annotated[Decimal, Field(description="US dollar amount, 2 decimal places")]
"""A USD amount. Use Decimal so rounding is deterministic across calc → output."""

SSN = Annotated[
    str,
    StringConstraints(pattern=r"^\d{3}-?\d{2}-?\d{4}$"),
    Field(description="Social Security Number, ddd-dd-dddd or ddddddddd"),
]

EIN = Annotated[
    str,
    StringConstraints(pattern=r"^\d{2}-?\d{7}$"),
    Field(description="Employer Identification Number, dd-ddddddd"),
]

StateCode = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Z]{2}$", min_length=2, max_length=2),
    Field(description="Two-letter USPS state code"),
]

ZipCode = Annotated[
    str,
    StringConstraints(pattern=r"^\d{5}(-\d{4})?$"),
    Field(description="US ZIP, ddddd or ddddd-dddd"),
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class FilingStatus(str, Enum):
    SINGLE = "single"
    MFJ = "mfj"  # married filing jointly
    MFS = "mfs"  # married filing separately
    HOH = "hoh"  # head of household
    QSS = "qss"  # qualifying surviving spouse


class ResidencyStatus(str, Enum):
    RESIDENT = "resident"
    NONRESIDENT = "nonresident"
    PART_YEAR = "part_year"


class DependentRelationship(str, Enum):
    SON = "son"
    DAUGHTER = "daughter"
    STEPCHILD = "stepchild"
    FOSTER_CHILD = "foster_child"
    SIBLING = "sibling"
    PARENT = "parent"
    GRANDPARENT = "grandparent"
    GRANDCHILD = "grandchild"
    NIECE_NEPHEW = "niece_nephew"
    OTHER = "other"


class CoverageType(str, Enum):
    SELF = "self"
    FAMILY = "family"


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


class Address(_StrictModel):
    street1: str
    street2: str | None = None
    city: str
    state: StateCode
    zip: ZipCode
    country: str = "US"
    county: str | None = None
    """County / local-subdivision name. Optional for most filers but
    **required** by states with county-level local income tax (e.g.
    Maryland — see `skill/scripts/states/md.py`). Canonical form is
    lower-case without punctuation or the " County" suffix (e.g.
    "baltimore city", "anne arundel", "prince georges"). Callers who
    don't need it can omit it; the MD plugin falls back to the 2.25%
    nonresident default rate when county is absent."""


class Person(_StrictModel):
    first_name: str
    middle_initial: str | None = None
    last_name: str
    ssn: SSN
    date_of_birth: dt.date
    date_of_death: dt.date | None = None
    """Required when this person is the spouse on a QSS (Qualifying Surviving Spouse) return."""
    is_blind: bool = False
    is_age_65_or_older: bool | None = None
    """If not set, caller should derive from date_of_birth and the tax year."""
    occupation: str | None = None
    ny_workdays_in_ny: int | None = None
    """NY IT-203-B workday apportionment numerator: the number of days
    actually worked in New York State (not just days present). Used by
    the NY plugin's nonresident path to compute the Schedule A
    allocation percentage = ny_workdays / total_workdays. When None,
    the NY plugin falls back to summing W-2 ``state_rows[].state_wages``
    where ``state == 'NY'`` for the sourced-wages estimate."""


class Dependent(_StrictModel):
    person: Person
    relationship: DependentRelationship
    months_lived_with_taxpayer: int = Field(ge=0, le=12)
    is_qualifying_child: bool
    is_qualifying_relative: bool
    is_student: bool = False
    is_disabled: bool = False
    claimed_by_other: bool = False

    @model_validator(mode="after")
    def _exactly_one_qualifying_kind(self) -> "Dependent":
        if self.is_qualifying_child and self.is_qualifying_relative:
            raise ValueError("dependent cannot be both qualifying child and qualifying relative")
        if not self.is_qualifying_child and not self.is_qualifying_relative:
            raise ValueError("dependent must be either qualifying child or qualifying relative")
        return self


# ---------------------------------------------------------------------------
# Income documents
# ---------------------------------------------------------------------------


class W2Box12Entry(_StrictModel):
    code: Annotated[str, StringConstraints(pattern=r"^[A-Z]{1,3}$")]
    amount: Money


class W2StateRow(_StrictModel):
    """A single state row on a W-2 (box 15/16/17).

    W-2 Copy 2 can list multiple state rows when a taxpayer worked in more than
    one state for the same employer. Each row has its own state code, wages, and
    withholding. The list form allows lossless multi-state W-2 ingestion.
    """

    state: StateCode
    state_wages: Money = Decimal("0")
    state_tax_withheld: Money = Decimal("0")
    state_employer_id: str | None = None
    locality: str | None = None
    local_wages: Money = Decimal("0")
    local_tax_withheld: Money = Decimal("0")


class W2(_StrictModel):
    """Form W-2, Wage and Tax Statement."""

    employer_name: str
    employer_ein: EIN | None = None
    employee_is_taxpayer: bool = True
    """True if attached to primary taxpayer, False if spouse."""

    box1_wages: Money
    box2_federal_income_tax_withheld: Money = Decimal("0")
    box3_social_security_wages: Money = Decimal("0")
    box4_social_security_tax_withheld: Money = Decimal("0")
    box5_medicare_wages: Money = Decimal("0")
    box6_medicare_tax_withheld: Money = Decimal("0")
    box7_social_security_tips: Money = Decimal("0")
    box8_allocated_tips: Money = Decimal("0")
    box10_dependent_care_benefits: Money = Decimal("0")
    box11_nonqualified_plans: Money = Decimal("0")
    box12_entries: list[W2Box12Entry] = Field(default_factory=list)
    box13_statutory_employee: bool = False
    box13_retirement_plan: bool = False
    box13_third_party_sick_pay: bool = False
    box14_other: list[str] = Field(default_factory=list)

    # OBBBA Schedule 1-A structured employer-attested inputs.
    # Employers who attest qualified tips or qualified overtime under
    # IRC §224 (OBBBA) report these amounts in a structured W-2 box 14
    # sub-field. Wave 4's Schedule 1-A patch treats
    # `AdjustmentsToIncome.qualified_tips_deduction_schedule_1a` as the
    # raw caller-supplied number because the ingestion layer couldn't
    # distinguish qualifying tips from `box7_social_security_tips` (which
    # includes non-qualifying tips). These box-14 OBBBA fields let the
    # ingester populate the structured amount directly when the employer
    # attests it, and let `_any_tips_or_overtime_declared` detect OBBBA-
    # eligible W-2s without the caller having to hand-populate the
    # adjustment field.
    box14_qualified_tips_obbba: Money = Decimal("0")
    """Employer-attested qualified tips per OBBBA §224 (Schedule 1-A
    line 1). Zero if the employer has not attested any qualifying tips
    on the W-2 — not every tipped job generates OBBBA-qualifying tips
    (the OBBBA IRS guidance lists qualifying occupations). Until the
    ingestion layer gains structured box-14 parsing, this field is
    caller-populated."""

    box14_qualified_overtime_obbba: Money = Decimal("0")
    """Employer-attested qualified overtime per OBBBA §225 (Schedule
    1-A line 2). Same semantics as `box14_qualified_tips_obbba` — only
    the FLSA §207 half-time premium portion qualifies, not straight-
    time overtime hours."""

    state_rows: list[W2StateRow] = Field(default_factory=list)
    """W-2 box 15/16/17 (+ 18/19/20 locality) as a list to support multi-state."""


class Form1099INT(_StrictModel):
    """Form 1099-INT, Interest Income."""

    payer_name: str
    payer_tin: str | None = None
    recipient_is_taxpayer: bool = True
    box1_interest_income: Money = Decimal("0")
    box2_early_withdrawal_penalty: Money = Decimal("0")
    box3_us_savings_bond_and_treasury_interest: Money = Decimal("0")
    box4_federal_income_tax_withheld: Money = Decimal("0")
    box5_investment_expenses: Money = Decimal("0")
    box6_foreign_tax_paid: Money = Decimal("0")
    box8_tax_exempt_interest: Money = Decimal("0")
    box9_specified_private_activity_bond_interest: Money = Decimal("0")
    box13_bond_premium_on_tax_exempt_bonds: Money = Decimal("0")


class Form1099DIV(_StrictModel):
    """Form 1099-DIV, Dividends and Distributions."""

    payer_name: str
    payer_tin: str | None = None
    recipient_is_taxpayer: bool = True
    box1a_ordinary_dividends: Money = Decimal("0")
    box1b_qualified_dividends: Money = Decimal("0")
    box2a_total_capital_gain_distributions: Money = Decimal("0")
    box2b_unrecaptured_sec_1250_gain: Money = Decimal("0")
    box2c_section_1202_gain: Money = Decimal("0")
    box2d_collectibles_28pct_gain: Money = Decimal("0")
    box3_nondividend_distributions: Money = Decimal("0")
    box4_federal_income_tax_withheld: Money = Decimal("0")
    box5_section_199a_dividends: Money = Decimal("0")
    box6_investment_expenses: Money = Decimal("0")
    box7_foreign_tax_paid: Money = Decimal("0")
    box11_exempt_interest_dividends: Money = Decimal("0")
    box12_specified_private_activity_bond_interest_dividends: Money = Decimal("0")


class Form1099BTransaction(_StrictModel):
    """A single line on Form 8949 / Schedule D, imported from a 1099-B broker statement."""

    description: str
    date_acquired: dt.date | Literal["various"] | None = None
    date_sold: dt.date
    proceeds: Money
    cost_basis: Money
    wash_sale_loss_disallowed: Money = Decimal("0")
    accrued_market_discount: Money = Decimal("0")
    is_long_term: bool
    basis_reported_to_irs: bool = True
    adjustment_codes: list[str] = Field(default_factory=list)
    adjustment_amount: Money = Decimal("0")
    form_8949_box_code: Literal["A", "B", "C", "D", "E", "F"] | None = None
    """Form 8949 box classification. If omitted, the Schedule D / 8949
    renderer auto-picks based on ``is_long_term`` and
    ``basis_reported_to_irs``: long-term + basis reported -> D,
    long-term + not reported -> E, short-term + basis reported -> A,
    short-term + not reported -> B. Set explicitly to 'C' (short-term)
    or 'F' (long-term) for a transaction NOT reported on a 1099-B
    at all (a private sale entered manually). The digital-asset
    boxes G/H/I/J/K/L are NOT yet modelled — wave 7 will add a
    Form1099DA model for those."""


class Form1099B(_StrictModel):
    """Form 1099-B, Proceeds From Broker and Barter Exchange Transactions."""

    broker_name: str
    recipient_is_taxpayer: bool = True
    transactions: list[Form1099BTransaction] = Field(default_factory=list)
    box4_federal_income_tax_withheld: Money = Decimal("0")


class Form1099NEC(_StrictModel):
    """Form 1099-NEC, Nonemployee Compensation. Flows into Schedule C."""

    payer_name: str
    payer_tin: str | None = None
    recipient_is_taxpayer: bool = True
    box1_nonemployee_compensation: Money
    box4_federal_income_tax_withheld: Money = Decimal("0")
    linked_schedule_c: str | None = None
    """Name of the Schedule C business this 1099 flows into."""


class Form1099R(_StrictModel):
    """Form 1099-R, Distributions From Pensions, Annuities, Retirement, etc.

    Typed stub — fan-out will extend. Pension/retirement distributions flow to
    1040 line 5a/5b (pensions), 4a/4b (IRAs), or Schedule 1 depending on code.
    """

    payer_name: str
    payer_tin: str | None = None
    recipient_is_taxpayer: bool = True
    box1_gross_distribution: Money = Decimal("0")
    box2a_taxable_amount: Money = Decimal("0")
    box2b_taxable_amount_not_determined: bool = False
    box2b_total_distribution: bool = False
    box4_federal_income_tax_withheld: Money = Decimal("0")
    box7_distribution_codes: list[str] = Field(default_factory=list)
    box7_ira_sep_simple: bool = False
    box9a_percent_total_distribution: float | None = None
    box12_state_tax_withheld: Money = Decimal("0")
    box13_state: StateCode | None = None
    box16_state_distribution: Money = Decimal("0")


class FormSSA1099(_StrictModel):
    """Form SSA-1099, Social Security Benefit Statement.

    Taxable portion of SS benefits is computed via the SS benefits worksheet.
    """

    recipient_is_taxpayer: bool = True
    box3_total_benefits: Money = Decimal("0")
    box4_benefits_repaid: Money = Decimal("0")
    box5_net_benefits: Money = Decimal("0")
    box6_federal_income_tax_withheld: Money = Decimal("0")
    medicare_part_b_premiums: Money = Decimal("0")
    medicare_part_d_premiums: Money = Decimal("0")


class Form1099G(_StrictModel):
    """Form 1099-G, Certain Government Payments.

    Box 1 = unemployment compensation (Schedule 1 line 7).
    Box 2 = state/local tax refund (taxable only if itemized prior year).
    """

    payer_name: str
    payer_tin: str | None = None
    recipient_is_taxpayer: bool = True
    box1_unemployment_compensation: Money = Decimal("0")
    box2_state_or_local_income_tax_refund: Money = Decimal("0")
    box2_tax_year: int | None = None
    box4_federal_income_tax_withheld: Money = Decimal("0")
    box5_rtaa_payments: Money = Decimal("0")
    box6_taxable_grants: Money = Decimal("0")
    box7_agricultural_payments: Money = Decimal("0")


class Form1098(_StrictModel):
    """Form 1098, Mortgage Interest Statement.

    Reports mortgage interest received by a lender from a borrower.
    Flows to Schedule A line 8a/8b (home mortgage interest deduction).
    """

    lender_name: str
    lender_tin: str | None = None
    recipient_is_taxpayer: bool = True
    box1_mortgage_interest: Money = Decimal("0")
    box2_outstanding_principal: Money = Decimal("0")
    box3_mortgage_origination_date: str | None = None
    box4_refund_of_overpaid_interest: Money = Decimal("0")
    box5_mortgage_insurance_premiums: Money = Decimal("0")
    box6_points_paid_on_purchase: Money = Decimal("0")
    box9_number_of_properties: int | None = None
    box10_other: str | None = None
    box11_mortgage_acquisition_date: str | None = None


class Form1098E(_StrictModel):
    """Form 1098-E, Student Loan Interest Statement.

    Reports student loan interest received by a lender from a borrower.
    Flows to Schedule 1 line 21 (student loan interest deduction, capped
    at $2,500).
    """

    lender_name: str
    lender_tin: str | None = None
    recipient_is_taxpayer: bool = True
    box1_student_loan_interest: Money = Decimal("0")


class Form1098T(_StrictModel):
    """Form 1098-T, Tuition Statement.

    Reports qualified tuition and related expenses paid to an eligible
    educational institution. Flows to Form 8863 (education credits:
    American Opportunity and Lifetime Learning).
    """

    institution_name: str
    institution_ein: EIN | None = None
    student_ssn: SSN | None = None
    recipient_is_taxpayer: bool = True
    box1_payments_received: Money = Decimal("0")
    box4_adjustments_prior_year: Money = Decimal("0")
    box5_scholarships: Money = Decimal("0")
    box6_adjustments_to_scholarships: Money = Decimal("0")
    box7_includes_next_year_amounts: bool = False
    box8_half_time_student: bool = False
    box9_graduate_student: bool = False
    box10_insurance_contract_reimbursement: Money = Decimal("0")


class Form1099MISC(_StrictModel):
    """Form 1099-MISC, Miscellaneous Information.

    Reports rents, royalties, other income, medical/healthcare payments,
    crop insurance, and other miscellaneous income. Flows to Schedule E
    (rents/royalties), Schedule 1 line 8z (other income), or Schedule C
    if applicable.
    """

    payer_name: str
    payer_tin: str | None = None
    recipient_is_taxpayer: bool = True
    recipient_tin: str | None = None
    box1_rents: Money = Decimal("0")
    box2_royalties: Money = Decimal("0")
    box3_other_income: Money = Decimal("0")
    box4_federal_tax_withheld: Money = Decimal("0")
    box5_fishing_boat_proceeds: Money = Decimal("0")
    box6_medical_healthcare_payments: Money = Decimal("0")
    box7_payer_direct_sales: bool = False
    """True if payer made direct sales totaling $5,000+ of consumer
    products to recipient for resale (checkbox on the form)."""
    box8_substitute_payments: Money = Decimal("0")
    box9_crop_insurance: Money = Decimal("0")
    box10_gross_proceeds_attorney: Money = Decimal("0")
    box11_fish_purchased_for_resale: Money = Decimal("0")
    box12_section_409a_deferrals: Money = Decimal("0")
    box14_nonqualified_deferred_compensation: Money = Decimal("0")
    box15_state_tax_withheld: Money = Decimal("0")


class Form1099K(_StrictModel):
    """Form 1099-K, Payment Card and Third Party Network Transactions.

    Reports gross amount of payment card / third-party network
    transactions. For TY2025 the reporting threshold is $5,000.
    Flows to Schedule C (business income) or Schedule 1 (other income).
    """

    payer_name: str
    """Filer's name (PSE or EPF/other third party)."""
    payer_tin: str | None = None
    recipient_is_taxpayer: bool = True
    settlement_entity_name: str | None = None
    """PSE's name if different from the filer."""
    box1a_gross_amount: Money = Decimal("0")
    box1b_card_not_present: Money = Decimal("0")
    box2_merchant_category_code: str | None = None
    box3_number_of_payment_transactions: int | None = None
    box4_federal_tax_withheld: Money = Decimal("0")
    box5a_january: Money = Decimal("0")
    box5b_february: Money = Decimal("0")
    box5c_march: Money = Decimal("0")
    box5d_april: Money = Decimal("0")
    box5e_may: Money = Decimal("0")
    box5f_june: Money = Decimal("0")
    box5g_july: Money = Decimal("0")
    box5h_august: Money = Decimal("0")
    box5i_september: Money = Decimal("0")
    box5j_october: Money = Decimal("0")
    box5k_november: Money = Decimal("0")
    box5l_december: Money = Decimal("0")


class ScheduleK1(_StrictModel):
    """Schedule K-1 (Form 1065 for partnerships, 1120-S for S-corps).

    Typed stub — extend in fan-out. K-1 items flow to many 1040 lines depending
    on the box and the partnership/S-corp source.
    """

    source_name: str
    source_ein: EIN | None = None
    source_type: Literal["partnership", "s_corp", "estate_or_trust"] = "partnership"
    recipient_is_taxpayer: bool = True
    ordinary_business_income: Money = Decimal("0")
    net_rental_real_estate_income: Money = Decimal("0")
    other_net_rental_income: Money = Decimal("0")
    guaranteed_payments: Money = Decimal("0")
    interest_income: Money = Decimal("0")
    ordinary_dividends: Money = Decimal("0")
    qualified_dividends: Money = Decimal("0")
    royalties: Money = Decimal("0")
    short_term_capital_gain_loss: Money = Decimal("0")
    long_term_capital_gain_loss: Money = Decimal("0")
    section_179_deduction: Money = Decimal("0")
    box14_self_employment_earnings: Money = Decimal("0")
    qbi_qualified: bool = False
    section_199a_w2_wages: Money = Decimal("0")
    section_199a_ubia: Money = Decimal("0")
    other_items: dict[str, Any] = Field(default_factory=dict)


class ScheduleCExpenses(_StrictModel):
    """Schedule C Part II expense categories. Aligns to line numbers 8–27."""

    line8_advertising: Money = Decimal("0")
    line9_car_and_truck: Money = Decimal("0")
    line10_commissions_and_fees: Money = Decimal("0")
    line11_contract_labor: Money = Decimal("0")
    line12_depletion: Money = Decimal("0")
    line13_depreciation: Money = Decimal("0")
    line14_employee_benefit_programs: Money = Decimal("0")
    line15_insurance_not_health: Money = Decimal("0")
    line16a_mortgage_interest: Money = Decimal("0")
    line16b_other_interest: Money = Decimal("0")
    line17_legal_and_professional: Money = Decimal("0")
    line18_office_expense: Money = Decimal("0")
    line19_pension_and_profit_sharing: Money = Decimal("0")
    line20a_rent_vehicles_machinery_equipment: Money = Decimal("0")
    line20b_rent_other_business_property: Money = Decimal("0")
    line21_repairs_and_maintenance: Money = Decimal("0")
    line22_supplies: Money = Decimal("0")
    line23_taxes_and_licenses: Money = Decimal("0")
    line24a_travel: Money = Decimal("0")
    line24b_meals_50pct_deductible: Money = Decimal("0")
    line25_utilities: Money = Decimal("0")
    line26_wages: Money = Decimal("0")
    line27a_other_expenses: Money = Decimal("0")
    other_expense_detail: dict[str, Money] = Field(default_factory=dict)


class DepreciableAsset(_StrictModel):
    """A single depreciable asset tracked on Form 4562.

    Attached to a ScheduleC (or ScheduleE / Form 2106 in a later wave).
    The Form 4562 renderer groups the business's assets into Part I
    (§179), Part II (special / bonus depreciation), Part III (MACRS
    Section A for current-year additions / Section C for prior years),
    Part V (listed property), and Part VI (amortization). Which Part an
    asset lands in is computed from the flags below — callers populate
    the facts, not the form placement.
    """

    description: str
    date_placed_in_service: dt.date
    cost: Money
    business_use_pct: Decimal = Decimal("100")
    """Percentage (not fraction). Listed property under §280F must
    clear 50% to be eligible for MACRS / §179 / bonus."""
    macrs_class: Literal["3", "5", "7", "10", "15", "20", "25", "27.5", "39"] | None = None
    """IRS class life. ``None`` means the asset is not MACRS-depreciable
    — use this for intangibles amortized under §197 / §195 (Part VI)."""
    section_179_elected: Money = Decimal("0")
    bonus_depreciation_elected: bool = True
    """True means accept the default §168(k) bonus depreciation (40%
    for TY2025) on the post-§179 basis."""
    prior_year_depreciation: Money = Decimal("0")
    """Pull the asset into Part III Section C (prior-year MACRS)."""
    is_listed_property: bool = False
    is_suv_over_6000lb: bool = False
    """Subject to the $31,300 §179 sub-cap for TY2025."""


class HomeOffice(_StrictModel):
    """Home-office deduction inputs for a single Schedule C business.

    Wave 6 adds Form 8829 support. Taxpayers pick ONE method per home
    office per tax year:

    * ``simplified`` — $5 per square foot of business-use area, capped
      at 300 sq ft / $1,500. Reported directly on Schedule C line 30;
      no Form 8829 is filed.
    * ``regular`` — Form 8829 with actual expenses × business-use %
      (of home). Subject to the gross-income limitation (can't deduct
      more than Sch C line 29 net of non-home-office expenses); the
      excess carries forward on Part IV. Depreciation on the business
      portion of the home is computed via the Part III mini-worksheet
      (39-year straight-line, mid-month convention).

    Regular-method-only fields (``home_purchase_price`` etc.) are
    optional — if you don't supply a purchase basis, the renderer simply
    omits Part III (no home depreciation). Rent-paying filers supply
    ``rent_total`` and leave basis fields blank.
    """

    method: Literal["simplified", "regular"] = "simplified"
    business_sq_ft: Decimal
    total_home_sq_ft: Decimal

    # Regular-method-only expense inputs (full-year totals; the renderer
    # applies the business-use percentage for indirect expenses).
    home_purchase_price: Money | None = None
    home_purchase_date: dt.date | None = None
    home_land_value: Money | None = None
    """Land value included in purchase price. Excluded from depreciation basis."""
    mortgage_interest_total: Money = Decimal("0")
    """Total household mortgage interest (indirect, × business %)."""
    real_estate_taxes_total: Money = Decimal("0")
    """Total household real estate taxes (indirect, × business %)."""
    utilities_total: Money = Decimal("0")
    """Total household utilities (indirect, × business %)."""
    insurance_total: Money = Decimal("0")
    """Total household homeowners/renters insurance (indirect, × business %)."""
    repairs_total: Money = Decimal("0")
    """General household repairs and maintenance (indirect, × business %)."""
    rent_total: Money = Decimal("0")
    """Household rent paid (for renters; indirect, × business %)."""
    other_expenses_total: Money = Decimal("0")
    """Catch-all for other indirect home-office expenses (line 22)."""

    # Direct-only expenses (100% business portion — e.g., painting the
    # office room). Callers who only have household-level numbers should
    # leave these at zero.
    direct_mortgage_interest: Money = Decimal("0")
    direct_real_estate_taxes: Money = Decimal("0")
    direct_insurance: Money = Decimal("0")
    direct_repairs: Money = Decimal("0")
    direct_utilities: Money = Decimal("0")
    direct_rent: Money = Decimal("0")
    direct_other_expenses: Money = Decimal("0")

    # Carryovers from prior year's Form 8829 Part IV
    prior_year_operating_carryover: Money = Decimal("0")
    """From prior year Form 8829 line 43 (operating expense carryover)."""
    prior_year_excess_casualty_depreciation_carryover: Money = Decimal("0")
    """From prior year Form 8829 line 44 (excess casualty/depreciation carryover)."""

    # Daycare special case
    is_daycare_facility: bool = False
    daycare_hours_per_year: Decimal = Decimal("0")
    """Daycare facilities not used exclusively for business scale the
    area percentage by (daycare hours / 8,760). Leave zero for
    non-daycare home offices."""

    @model_validator(mode="after")
    def _sq_ft_sanity(self) -> "HomeOffice":
        if self.business_sq_ft < 0 or self.total_home_sq_ft < 0:
            raise ValueError("home office square footage must be non-negative")
        if self.total_home_sq_ft > 0 and self.business_sq_ft > self.total_home_sq_ft:
            raise ValueError(
                "home office business_sq_ft cannot exceed total_home_sq_ft"
            )
        return self


class ScheduleC(_StrictModel):
    """Form 1040 Schedule C — Profit or Loss From Business (Sole Proprietorship)."""

    proprietor_is_taxpayer: bool = True
    business_name: str
    principal_business_or_profession: str
    principal_business_code: str | None = None  # NAICS-style code, Part I line B
    ein: EIN | None = None
    business_address: Address | None = None
    business_location_state: StateCode | None = None
    """Primary state where the business operates. Used by state plugins
    to source Schedule C net profit to the correct state for nonresident
    / part-year returns (see ``states._hand_rolled_base.state_source_
    schedule_c``). Distinct from ``business_address.state`` — a business
    can have a mailing address in one state but operate primarily in
    another (home-office SE running inventory from a warehouse). When
    None, the plugin's ambiguous-sourcing fallback (day-proration) kicks
    in."""
    accounting_method: Literal["cash", "accrual", "other"] = "cash"
    material_participation: bool = True
    started_or_acquired_this_year: bool = False
    made_1099_payments: bool | None = None
    filed_required_1099s: bool | None = None

    line1_gross_receipts: Money = Decimal("0")
    line2_returns_and_allowances: Money = Decimal("0")
    line4_cost_of_goods_sold: Money = Decimal("0")
    line6_other_income: Money = Decimal("0")
    expenses: ScheduleCExpenses = Field(default_factory=ScheduleCExpenses)
    line30_home_office_expense: Money = Decimal("0")
    """From Form 8829 (regular method) or the $5/sq ft simplified method.

    Wave 6: if ``home_office`` is populated, the engine / pipeline will
    recompute this from the HomeOffice block via
    ``skill.scripts.output.form_8829.compute_home_office_deduction``.
    If you leave ``home_office`` ``None`` (wave 5 behavior) this field
    remains a caller-supplied pass-through."""
    line32_at_risk_box: Literal["all_at_risk", "some_not_at_risk"] = "all_at_risk"

    # Depreciation / Form 4562 inputs (Wave 6 Agent 4)
    depreciable_assets: list[DepreciableAsset] = Field(default_factory=list)
    """Assets tracked on this business's Form 4562. The calc module
    aggregates these into Part I/II/III/V/VI and writes the total
    into Schedule C line 13 — callers should NOT also hand-populate
    ``expenses.line13_depreciation`` when ``depreciable_assets`` is
    non-empty."""

    section_179_carryover_from_prior_year: Money = Decimal("0")
    """Per-business §179 carryforward from prior year. Zero means
    "no carryover to apply on this business"; the Form 4562 Part I
    line 10 uses this value directly."""

    home_office: HomeOffice | None = None
    """Wave 6 — optional home-office block. When populated, the Form 8829
    renderer derives the Schedule C line 30 amount (regular method) or
    applies the $5/sq ft simplified cap, and the pipeline emits Form
    8829 as a paper-bundle attachment for regular-method filers."""


class Form1095AMonthly(_StrictModel):
    """One month's data from Form 1095-A."""

    enrollment_premium: Money = Decimal("0")
    slcsp_premium: Money = Decimal("0")
    advance_ptc: Money = Decimal("0")


class Form1095A(_StrictModel):
    """Health Insurance Marketplace Statement."""

    marketplace_id: str = ""
    policy_start_date: dt.date | None = None
    policy_end_date: dt.date | None = None
    monthly_data: list[Form1095AMonthly] = Field(default_factory=list)
    """Up to 12 entries, one per covered month."""


class ScheduleEProperty(_StrictModel):
    """A single rental real estate property on Schedule E Part I."""

    address: Address
    property_type: Literal[
        "single_family",
        "multi_family",
        "vacation_short_term",
        "commercial",
        "land",
        "self_rental",
        "other",
    ] = "single_family"
    fair_rental_days: int = Field(default=0, ge=0, le=366)
    personal_use_days: int = Field(default=0, ge=0, le=366)
    qbi_qualified: bool = False

    rents_received: Money = Decimal("0")
    royalties_received: Money = Decimal("0")
    advertising: Money = Decimal("0")
    auto_and_travel: Money = Decimal("0")
    cleaning_and_maintenance: Money = Decimal("0")
    commissions: Money = Decimal("0")
    insurance: Money = Decimal("0")
    legal_and_professional: Money = Decimal("0")
    management_fees: Money = Decimal("0")
    mortgage_interest_to_banks: Money = Decimal("0")
    other_interest: Money = Decimal("0")
    repairs: Money = Decimal("0")
    supplies: Money = Decimal("0")
    taxes: Money = Decimal("0")
    utilities: Money = Decimal("0")
    depreciation: Money = Decimal("0")
    """Computed via Form 4562 by the depreciation module (MACRS)."""
    other_expenses: dict[str, Money] = Field(default_factory=dict)


class ScheduleE(_StrictModel):
    """Form 1040 Schedule E — Supplemental Income and Loss."""

    properties: list[ScheduleEProperty] = Field(default_factory=list)
    part_ii_partnership_s_corp: list[dict[str, Any]] = Field(default_factory=list)
    """Stub: Part II K-1 passthroughs. Tighten in fan-out."""
    part_iii_estates_trusts: list[dict[str, Any]] = Field(default_factory=list)
    """Stub: Part III K-1 estate/trust. Tighten in fan-out."""


# ---------------------------------------------------------------------------
# Form 4797 — Sales of Business Property
# ---------------------------------------------------------------------------


class Form4797Sale(_StrictModel):
    """A single sale of business property reported on Form 4797.

    Form 4797 reports gains/losses from the sale of business property,
    rental property, and depreciable assets.  The ``section_type`` field
    determines which Part of Form 4797 the sale lands in:

    * ``"1231"`` — Part I: gains/losses from sales of property held more
      than one year (§1231 assets). Net §1231 gains may be treated as
      long-term capital gains; net §1231 losses are ordinary.
    * ``"1245"`` — Part II: ordinary gains/losses from sale of personal
      property (tangible and intangible) subject to §1245 depreciation
      recapture. All depreciation is recaptured as ordinary income; any
      gain above the depreciation is §1231 gain.
    * ``"1250"`` — Part III: gain from sale of real property subject to
      §1250 depreciation recapture. Unrecaptured §1250 gain (the
      straight-line depreciation portion) is taxed at a maximum 25%
      rate; excess gain is §1231 gain.

    Flows to: Schedule 1 line 4 (other gains/losses), Schedule D (when
    §1231 gain is treated as capital gain).

    Authority: IRS Form 4797 (TY2025), Instructions for Form 4797.
    """

    description: str
    """Description of the property (e.g., "Office Equipment", "Rental House")."""
    date_acquired: dt.date | Literal["various"] | None = None
    """Date the property was acquired. Use ``"various"`` for aggregated lots."""
    date_sold: dt.date
    """Date the property was sold or disposed of."""
    gross_sales_price: Money
    """Gross proceeds from the sale (Form 4797 column (d))."""
    cost_or_basis: Money
    """Original cost or other basis (Form 4797 column (e))."""
    depreciation_allowed: Money = Decimal("0")
    """Total depreciation allowed or allowable (Form 4797 column (f)).
    Drives the §1245/§1250 recapture computation."""
    section_type: Literal["1231", "1245", "1250"]
    """Which IRC section governs the sale. Determines Part placement on
    Form 4797 and the recapture computation method."""


# ---------------------------------------------------------------------------
# Adjustments, deductions, credits
# ---------------------------------------------------------------------------


class AdjustmentsToIncome(_StrictModel):
    """Schedule 1 Part II — above-the-line adjustments. Includes OBBBA additions."""

    educator_expenses: Money = Decimal("0")
    hsa_deduction: Money = Decimal("0")
    deductible_se_tax: Money = Decimal("0")  # computed from Sch SE, do not set manually
    se_health_insurance: Money = Decimal("0")
    se_retirement_plans: Money = Decimal("0")  # SEP, SIMPLE, Solo 401k
    alimony_paid: Money = Decimal("0")  # pre-2019 divorces only
    alimony_recipient_ssn: SSN | None = None
    alimony_divorce_date: dt.date | None = None
    ira_deduction: Money = Decimal("0")
    student_loan_interest: Money = Decimal("0")
    archer_msa_deduction: Money = Decimal("0")
    penalty_on_early_withdrawal_of_savings: Money = Decimal("0")
    moving_expenses_military: Money = Decimal("0")

    # OBBBA additions (TY2025 and forward)
    qualified_tips_deduction_schedule_1a: Money = Decimal("0")
    """OBBBA Schedule 1-A qualified tips deduction (TY2025-2028)."""
    qualified_overtime_deduction_schedule_1a: Money = Decimal("0")
    """OBBBA Schedule 1-A qualified overtime deduction (TY2025-2028)."""
    senior_deduction_obbba: Money = Decimal("0")
    """OBBBA +$6,000 age 65+ deduction (TY2025-2028). Computed, not set manually."""
    trump_account_deduction_form_4547: Money = Decimal("0")
    """OBBBA Form 4547 Trump Account election deduction."""

    other_adjustments: dict[str, Money] = Field(default_factory=dict)


class AMTAdjustments(_StrictModel):
    """Form 6251 AMT-specific adjustments and preferences.

    Optional block on :class:`CanonicalReturn` for taxpayers with AMT
    preference items that the engine cannot derive from the regular
    return. The SALT add-back on Form 6251 line 2a is handled
    automatically by the renderer (it reads Schedule A line 7); these
    fields cover the OTHER preferences the calc engine does not model:

    * **iso_bargain_element** — Line 2i. The excess of the ISO stock's
      fair market value over the exercise price on the date of exercise,
      for shares exercised but NOT sold in the same tax year. Positive
      amounts push AMTI up.
    * **private_activity_bond_interest** — Line 2g. Interest from
      specified private activity bonds that is exempt from regular tax
      but not from AMT. Also populated indirectly from
      ``Form1099INT.box9_specified_private_activity_bond_interest`` when
      present, but this manual field lets a caller override or add to
      that.
    * **depreciation_adjustment** — Line 2l. Difference between regular-
      tax and AMT depreciation on assets placed in service after 1986.
      Positive = AMT depreciation is less than regular (AMTI goes up);
      can be negative in later recovery years.
    * **other_prefs** — Catch-all for the remaining Line 2c/2d/2e/2h/
      2j/2k/2m/2n/2o/2p/2q/2r/2t preferences plus line 3 "Other
      adjustments". Sum is added to AMTI. Keep per-line breakdown in the
      dict for traceability.

    Authority: IRS Form 6251 TY2025, Part I lines 2a-2t and 3. See
    https://www.irs.gov/pub/irs-pdf/f6251.pdf and the accompanying
    instructions at https://www.irs.gov/pub/irs-pdf/i6251.pdf.
    """

    iso_bargain_element: Money = Decimal("0")
    """Line 2i — incentive stock option bargain element (FMV at
    exercise - strike price) for ISOs exercised but held past year end."""

    private_activity_bond_interest: Money = Decimal("0")
    """Line 2g — interest from specified private activity bonds that is
    exempt from regular tax but taxable under the AMT."""

    depreciation_adjustment: Money = Decimal("0")
    """Line 2l — post-1986 depreciation timing difference. Positive
    increases AMTI; can be negative as regular-tax recovery catches up
    with AMT recovery in later years."""

    other_prefs: dict[str, Money] = Field(default_factory=dict)
    """Catch-all for the remaining Form 6251 line 2/line 3 preference
    items. Every entry is added to AMTI as-is; negative values are
    respected for lines 2b/2f/2s which enter as subtractions."""


class IRAInfo(_StrictModel):
    """Form 8606 — IRA basis tracking.

    Tracks nondeductible contributions to traditional IRAs, basis
    carryover, and the nontaxable/taxable split of distributions and
    Roth conversions.  Required when a taxpayer makes nondeductible
    contributions, takes distributions from a traditional IRA with
    basis, or converts a traditional IRA to Roth with basis.

    Authority: IRS Form 8606 (TY2025).
    """

    nondeductible_contributions_current_year: Money = Decimal("0")
    """Line 1: nondeductible contributions made for the tax year."""
    prior_year_basis: Money = Decimal("0")
    """Line 2: total basis carryover from prior year Form 8606 line 14."""
    contributions_withdrawn_by_due_date: Money = Decimal("0")
    """Line 4: contributions withdrawn before the filing due date."""
    total_ira_value_year_end: Money = Decimal("0")
    """Line 6: value of ALL traditional IRAs as of Dec 31."""
    distributions_received: Money = Decimal("0")
    """Line 7: total traditional IRA distributions received during the year."""
    roth_conversions: Money = Decimal("0")
    """Line 8: amount converted from traditional IRA to Roth IRA."""


class ItemizedDeductions(_StrictModel):
    """Schedule A — Itemized Deductions.

    Note: the taxpayer must choose state/local INCOME tax OR state/local SALES tax
    (not both). Whichever is larger is typically elected. The SALT (state +
    local + real estate + personal property) total is CAPPED at $10,000 MFJ/S/HoH
    or $5,000 MFS. The cap is applied in the calc engine, not in this model.
    """

    medical_and_dental_total: Money = Decimal("0")
    state_and_local_income_tax: Money = Decimal("0")
    state_and_local_sales_tax: Money = Decimal("0")
    elect_sales_tax_over_income_tax: bool = False
    """If True, use state_and_local_sales_tax; else state_and_local_income_tax."""
    real_estate_tax: Money = Decimal("0")
    personal_property_tax: Money = Decimal("0")
    home_mortgage_interest: Money = Decimal("0")
    mortgage_points: Money = Decimal("0")
    mortgage_insurance_premiums: Money = Decimal("0")
    investment_interest: Money = Decimal("0")
    gifts_to_charity_cash: Money = Decimal("0")
    gifts_to_charity_other_than_cash: Money = Decimal("0")
    gifts_to_charity_carryover: Money = Decimal("0")
    casualty_and_theft_losses_federal_disaster: Money = Decimal("0")
    other_itemized: dict[str, Money] = Field(default_factory=dict)


class DependentCareExpenses(_StrictModel):
    """Form 2441 — Child and Dependent Care Expenses."""

    care_providers: list[dict[str, Any]] = Field(default_factory=list)
    """List of care provider dicts with name, address, tin, amount_paid."""
    qualifying_persons: int = Field(default=0, ge=0)
    """Number of qualifying persons (children under 13 or disabled dependents)."""
    total_expenses_paid: Money = Decimal("0")
    """Total dependent care expenses paid during the tax year."""
    employer_benefits_excluded: Money = Decimal("0")
    """Box 10 of W-2 — dependent care benefits excluded from income."""


class EducationStudent(_StrictModel):
    """One student for Form 8863."""
    name: str
    ssn: SSN
    institution_name: str = ""
    qualified_expenses: Money = Decimal("0")
    is_aotc_eligible: bool = True
    completed_4_years: bool = False
    half_time_student: bool = True
    felony_drug_conviction: bool = False


class EducationCredits(_StrictModel):
    """Form 8863 input block."""
    students: list[EducationStudent] = Field(default_factory=list)


class Credits(_StrictModel):
    child_tax_credit: Money = Decimal("0")  # computed
    additional_child_tax_credit_refundable: Money = Decimal("0")  # computed
    credit_for_other_dependents: Money = Decimal("0")
    dependent_care_credit: Money = Decimal("0")
    education_credits_nonrefundable: Money = Decimal("0")  # AOTC + LLC non-refundable
    education_credits_refundable: Money = Decimal("0")  # AOTC refundable portion
    retirement_savings_credit: Money = Decimal("0")  # Form 8880
    foreign_tax_credit: Money = Decimal("0")
    residential_energy_credits: Money = Decimal("0")
    earned_income_tax_credit: Money = Decimal("0")  # computed
    premium_tax_credit_net: Money = Decimal("0")
    other_credits: dict[str, Money] = Field(default_factory=dict)


class OtherTaxes(_StrictModel):
    self_employment_tax: Money = Decimal("0")  # from Schedule SE
    additional_medicare_tax: Money = Decimal("0")  # from Form 8959
    net_investment_income_tax: Money = Decimal("0")  # from Form 8960
    alternative_minimum_tax: Money = Decimal("0")  # from Form 6251
    early_distribution_penalty: Money = Decimal("0")  # from Form 5329
    other: dict[str, Money] = Field(default_factory=dict)


class Payments(_StrictModel):
    """Form 1040 lines 25 and 31 — payments / credits against tax.

    Convention: W-2 federal withholding is read directly from the W-2 list
    (w2s[].box2_federal_income_tax_withheld). Do NOT also set
    federal_income_tax_withheld_from_w2 — it's kept for cases where the user
    enters an aggregate without itemized W-2s, and the calc engine warns if
    both are non-zero.
    """

    federal_income_tax_withheld_from_w2: Money = Decimal("0")
    """Optional aggregate. Prefer populating w2s[].box2 and leaving this at 0."""
    federal_income_tax_withheld_from_1099: Money = Decimal("0")
    federal_income_tax_withheld_other: Money = Decimal("0")  # SSA, RRB, gambling
    estimated_tax_payments_2025: Money = Decimal("0")
    prior_year_overpayment_applied: Money = Decimal("0")
    amount_paid_with_4868_extension: Money = Decimal("0")
    excess_social_security_tax_withheld: Money = Decimal("0")
    earned_income_credit_refundable: Money = Decimal("0")  # from EITC calc
    additional_child_tax_credit_refundable: Money = Decimal("0")  # from CTC calc
    american_opportunity_credit_refundable: Money = Decimal("0")  # from AOTC


# ---------------------------------------------------------------------------
# State returns
# ---------------------------------------------------------------------------


class StateReturn(_StrictModel):
    state: StateCode
    residency: ResidencyStatus
    days_in_state: int = Field(ge=0, le=366)
    state_specific: dict[str, Any] = Field(default_factory=dict)
    """All plugins must include ``state_total_tax`` (Decimal or int).
    Per-state extensions are allowed beyond that base key."""

    @model_validator(mode="after")
    def _require_state_total_tax(self) -> "StateReturn":
        if "state_total_tax" not in self.state_specific:
            raise ValueError(
                f"state_specific for {self.state} must include 'state_total_tax'"
            )
        return self


# ---------------------------------------------------------------------------
# Carryforwards
# ---------------------------------------------------------------------------


class PriorYearCarryforwards(_StrictModel):
    nol_carryforward: Money = Decimal("0")
    short_term_capital_loss_carryover: Money = Decimal("0")
    long_term_capital_loss_carryover: Money = Decimal("0")
    charitable_contribution_carryover: Money = Decimal("0")
    amt_credit_carryforward: Money = Decimal("0")
    passive_activity_loss_carryover: Money = Decimal("0")
    foreign_tax_credit_carryover: Money = Decimal("0")
    section_179_disallowed_carryover: Money = Decimal("0")
    other: dict[str, Money] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Computed totals (populated by calc engine)
# ---------------------------------------------------------------------------


class ComputedTotals(_StrictModel):
    """Fields populated by the calc engine. Absent on a fresh interview; filled after compute.

    `computed_input_hash` is a stamp of the input fields that produced this
    result. If the canonical return is mutated after compute() runs, downstream
    consumers can detect stale computed totals by re-hashing the inputs and
    comparing.
    """

    total_income: Money | None = None
    adjustments_total: Money | None = None
    adjusted_gross_income: Money | None = None
    deduction_taken: Money | None = None  # max(standard, itemized) + senior + OBBBA extras
    qbi_deduction: Money | None = None
    taxable_income: Money | None = None
    tentative_tax: Money | None = None
    total_credits_nonrefundable: Money | None = None
    alternative_minimum_tax: Money | None = None
    """Form 6251 line 11 — additional tax owed beyond regular tax when
    tentative minimum tax exceeds regular tax. Added into ``total_tax``
    by the engine when the Form 6251 compute path fires. ``None`` when
    AMT was not computed (no trigger items). Zero when computed but
    below or equal to regular tax."""
    other_taxes_total: Money | None = None
    total_tax: Money | None = None
    total_payments: Money | None = None
    refund: Money | None = None
    amount_owed: Money | None = None
    effective_rate: float | None = None
    marginal_rate: float | None = None
    computed_input_hash: str | None = None
    """Hash of the input fields used to produce this result. If the model is
    mutated after compute(), a fresh hash will differ and consumers can
    recompute. Set by the calc engine; do not populate manually."""

    validation_report: dict[str, Any] | None = None
    """Opaque JSON-serializable validation report produced by
    `skill.scripts.validate.run_return_validation`. Today it carries the
    FFFF compatibility report under the `ffff` key; future validation
    passes (schema cross-checks, missing-document warnings, state-level
    rules) will add new top-level keys additively. Downstream consumers
    should treat the dict as opaque. Set by the calc engine."""


# ---------------------------------------------------------------------------
# Root
# ---------------------------------------------------------------------------


class CanonicalReturn(_StrictModel):
    """Root of the canonical tax return JSON."""

    schema_version: Literal["0.1.0"] = SCHEMA_VERSION
    tax_year: int = Field(ge=2024, le=2030)

    filing_status: FilingStatus
    taxpayer: Person
    spouse: Person | None = None
    address: Address
    phone: str | None = None
    email: str | None = None

    dependents: list[Dependent] = Field(default_factory=list)

    # Income
    w2s: list[W2] = Field(default_factory=list)
    forms_1099_int: list[Form1099INT] = Field(default_factory=list)
    forms_1099_div: list[Form1099DIV] = Field(default_factory=list)
    forms_1099_b: list[Form1099B] = Field(default_factory=list)
    forms_1099_nec: list[Form1099NEC] = Field(default_factory=list)
    forms_1099_r: list[Form1099R] = Field(default_factory=list)
    forms_1099_g: list[Form1099G] = Field(default_factory=list)
    forms_1099_misc: list[Form1099MISC] = Field(default_factory=list)
    forms_1099_k: list[Form1099K] = Field(default_factory=list)
    forms_ssa_1099: list[FormSSA1099] = Field(default_factory=list)
    forms_1098: list[Form1098] = Field(default_factory=list)
    forms_1098_e: list[Form1098E] = Field(default_factory=list)
    forms_1098_t: list[Form1098T] = Field(default_factory=list)
    schedules_c: list[ScheduleC] = Field(default_factory=list)
    schedules_e: list[ScheduleE] = Field(default_factory=list)
    schedules_k1: list[ScheduleK1] = Field(default_factory=list)

    # Form 4797 — Sales of Business Property
    forms_4797: list[Form4797Sale] = Field(default_factory=list)
    """Sales of business property reported on Form 4797. Each entry
    represents one disposition; the Form 4797 renderer classifies them
    into Parts I/II/III based on ``section_type`` and computes the
    depreciation recapture, §1231 gain/loss, and unrecaptured §1250
    gain. Net results flow to Schedule 1 line 4 (other gains/losses)
    and, when applicable, to Schedule D (§1231 net gain treated as
    long-term capital gain)."""

    # ACA marketplace statements
    forms_1095_a: list[Form1095A] = Field(default_factory=list)

    # Other income escape hatch (prefer typed forms above)
    other_income: dict[str, Any] = Field(default_factory=dict)
    """Catch-all for income types without a typed form yet. Prefer adding a
    typed model and moving out of here."""

    # Schedule B Part III — Foreign Accounts and Trusts
    has_foreign_financial_account_over_10k: bool = False
    """Schedule B Part III line 7a: at any time during the tax year,
    had a financial interest in or signature authority over a financial
    account located in a foreign country whose aggregate value exceeded
    $10,000 USD. True triggers FinCEN Form 114 (FBAR) filing requirement
    and forces Schedule B regardless of interest/dividend thresholds."""

    has_foreign_trust_transaction: bool = False
    """Schedule B Part III line 8: during the tax year, received a
    distribution from, or was grantor of, or transferor to, a foreign
    trust. True triggers Form 3520 filing requirement and forces
    Schedule B."""

    foreign_account_countries: list[str] = Field(default_factory=list)
    """Schedule B Part III line 7b: countries where foreign financial
    accounts are located. ISO 3166-1 alpha-2 codes (e.g. "FR", "JP")."""

    # Adjustments / deductions / credits / taxes / payments
    adjustments: AdjustmentsToIncome = Field(default_factory=AdjustmentsToIncome)
    itemize_deductions: bool = False
    itemized: ItemizedDeductions | None = None
    credits: Credits = Field(default_factory=Credits)
    other_taxes: OtherTaxes = Field(default_factory=OtherTaxes)
    payments: Payments = Field(default_factory=Payments)

    # Form 8606 — IRA basis tracking (nondeductible contributions,
    # distributions with basis, Roth conversions with basis). Optional;
    # omit when the taxpayer has no nondeductible IRA contributions and
    # no traditional IRA basis to track.
    ira_info: IRAInfo | None = None

    # Form 6251 AMT manual adjustments (ISOs, PAB interest, depreciation
    # timing, etc.) — optional; omit for taxpayers with no AMT preferences
    # beyond the SALT add-back (which the engine reads from itemized).
    amt_adjustments_manual: AMTAdjustments | None = None

    # Form 2441 — Child and Dependent Care Expenses
    dependent_care: DependentCareExpenses | None = None

    # Form 8863 — Education Credits (AOTC + LLC). Optional; omit when
    # the taxpayer has no education expenses to claim.
    education: EducationCredits | None = None

    # States
    state_returns: list[StateReturn] = Field(default_factory=list)

    # Carryforwards
    carryforwards: PriorYearCarryforwards = Field(default_factory=PriorYearCarryforwards)

    # Computed (populated by calc engine)
    computed: ComputedTotals = Field(default_factory=ComputedTotals)

    # Free-form notes (not tax data, just reminders for the human)
    notes: list[str] = Field(default_factory=list)

    @field_validator("tax_year")
    @classmethod
    def _tax_year_current(cls, v: int) -> int:
        if v < 2024:
            raise ValueError("tax_year must be >= 2024 (skill targets TY2025 and forward)")
        return v

    @model_validator(mode="after")
    def _spouse_required_iff_joint(self) -> "CanonicalReturn":
        needs_spouse = self.filing_status in (FilingStatus.MFJ, FilingStatus.MFS)
        if needs_spouse and self.spouse is None:
            raise ValueError(f"filing_status={self.filing_status.value} requires spouse")
        if not needs_spouse and self.spouse is not None and self.filing_status != FilingStatus.QSS:
            raise ValueError(f"filing_status={self.filing_status.value} should not have spouse")
        # QSS: a spouse is allowed but must be marked deceased with a date of death
        if self.filing_status == FilingStatus.QSS:
            if self.spouse is not None and self.spouse.date_of_death is None:
                raise ValueError(
                    "filing_status=qss with a spouse requires spouse.date_of_death "
                    "(Qualifying Surviving Spouse requires the spouse to be deceased)"
                )
        return self

    @model_validator(mode="after")
    def _itemized_iff_itemize(self) -> "CanonicalReturn":
        if self.itemize_deductions and self.itemized is None:
            raise ValueError("itemize_deductions=True requires itemized block")
        return self
