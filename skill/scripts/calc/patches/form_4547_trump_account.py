"""OBBBA Form 4547 — Trump Account Election(s) patch.

TY2025-2028 / OBBBA P.L. 119-21 (signed 2025-07-04) / IRC §§128, 219(g),
530A (Trump Accounts / Working Families Tax Cuts).

===============================================================================
IMPORTANT — THIS IS AN ELECTION FORM, NOT A DEDUCTION FORM
===============================================================================

After reviewing IRS primary guidance (Form 4547 (12/2025), Instructions for
Form 4547 (12/2025), IRS newsroom Trump Account guidance notice), plus
corroborating practitioner analyses from Skadden, Grant Thornton, Mercer,
Nixon Peabody, and SavingForCollege.com, the researcher's finding is:

    *** NO INDIVIDUAL DEDUCTION is allowed for any contribution to a
        Trump Account. IRC §219 explicitly disallows it. Form 4547
        itself contains NO dollar amounts — only checkboxes for (a) the
        initial account-opening election and (b) the $1,000 Pilot Program
        Contribution request. ***

The canonical return model has an ``AdjustmentsToIncome.trump_account_
deduction_form_4547`` field (models.py line 507) that was scaffolded on the
pre-statute assumption that the election might include a contribution
deduction. Based on final IRS guidance, that assumption is **incorrect**:
the field will always be $0.00 for any TY2025-2028 return and is kept on
the model only because this patch is not authorized to modify models.py.

===============================================================================
What this patch actually computes
===============================================================================

1. **Deduction = $0.00** — always, unconditionally. This is the current IRS
   rule per §219 and the Form 4547 instructions. It will remain $0 unless
   Congress adds a new deductible contribution regime.

2. **Qualifying child counting** — we count children who would be eligible
   for a Form 4547 Pilot Program Contribution election on this return:
   - Born 2025-01-01 through 2028-12-31 (pilot window).
   - Under 18 at end of tax year (Trump Account beneficiary age cap).
   - Marked as a qualifying child dependent on the return (proxy for
     "anticipated qualifying child" in the instructions).
   - Has a valid SSN (enforced by Person.ssn model validator).

3. **Year gating** — the patch returns 0 for tax years outside the OBBBA
   window (2025-2028). For TY2029+, even the election is unavailable in
   the current form; COL adjustments start 2027 per statute but the form
   window is the pilot window.

4. **Audit trail** — the ``details`` dict exposes counts, annual contribution
   cap, pilot-contribution amount, and a loud warning that the deduction
   is always $0 per §219.

===============================================================================
LOCKED ASSUMPTIONS — verified against IRS primary source
===============================================================================

These are CONFIRMED from IRS primary guidance (see sources), not unverified:

- V1: ``deduction`` is ALWAYS $0 (IRC §219 disallowance — instructions
  explicit). If final regulations ever add a deductible contribution, this
  patch must be updated and a constants entry added.
- V2: Pilot Program Contribution is $1,000 per eligible child (statute).
- V3: Annual aggregate contribution cap is $5,000 per beneficiary from
  all non-exception sources (cost-of-living adjusted after 2027 — Grant
  Thornton/Skadden/IRS instructions agree).
- V4: Employer §128 contribution cap is $2,500 per employee/year (Skadden,
  Paylocity, Mercer, Cornell Law §128). These are excluded from W-2 Box 1
  wages upstream — NOT an adjustment on the 1040. This patch does NOT
  compute that exclusion; it is the ingester's job.
- V5: Pilot window for $1,000 contribution is children born 2025-01-01
  through 2028-12-31 (IRS instructions explicit).

===============================================================================
UNVERIFIED ASSUMPTIONS — loudly locked in tests pending regulations
===============================================================================

- U1: ``years_applicable = [2025, 2026, 2027, 2028]`` — the IRS form and
  instructions were released 12/2025 and reference "the 2025 return". No
  explicit sunset is stated for the election; we lock the window to the
  pilot-contribution window because that is the only window with concrete
  statute language. Treasury/IRS proposed regulations (NPRM docket
  2026-04533) may extend or contract this. RE-VERIFY on final regulations.
- U2: Contributions are TIED TO A QUALIFYING CHILD on the filer's return.
  The instructions say "anticipated to be the qualifying child of the
  authorized individual making the election for that tax year." We use the
  ``Dependent.is_qualifying_child`` flag as proxy. This is tight enough for
  pilot-contribution eligibility counting but may be looser than the
  statute — e.g. a grandparent-authorized account where the grandparent is
  not the filer may still be electable by the grandparent. RE-VERIFY on
  final regulations, particularly for the guardian/sibling/grandparent
  priority path in Part I of the form.
- U3: Deduction-amount = $0 is permanent. Current IRS guidance is clear,
  but the OBBBA statute has several implementing regulations pending. If a
  NPRM in 2026 creates any deductible slice (for e.g. §529 rollover-style
  credits), this patch returns the wrong number. RE-VERIFY on each IRS
  release.
- U4: There are NO AGI/MAGI phase-outs on Trump Account eligibility. Final
  IRS guidance and every practitioner blog reviewed agree that there is
  no income-based phase-out for the account itself or the pilot
  contribution. The tests lock ``phase_out_reduction = 0`` always. If a
  phase-out appears in final regulations, this patch must be updated.

===============================================================================
References (cite on every commit)
===============================================================================

Primary IRS sources (PRESENT as of 2026-04-11):
  - https://www.irs.gov/forms-pubs/about-form-4547
  - https://www.irs.gov/instructions/i4547           (12/2025 instructions)
  - https://www.irs.gov/pub/irs-pdf/f4547.pdf        (12/2025 form, extracted)
  - https://www.irs.gov/newsroom/treasury-irs-issue-guidance-on-trump-accounts-established-under-the-working-families-tax-cuts-notice-announces-upcoming-regulations
  - https://www.federalregister.gov/documents/2026/03/09/2026-04533/trump-accounts
    (NPRM docket — regulations pending as of 2026-04-11)

Statute:
  - P.L. 119-21 Title VII (Working Families Tax Cuts / Trump Accounts)
  - IRC §128 (employer contribution exclusion)
  - IRC §219(g) (individual deduction disallowance)
  - https://www.law.cornell.edu/uscode/text/26/128

Practitioner corroboration:
  - https://www.skadden.com/insights/publications/2025/12/irs-issues-initial-guidance-regarding-trump-accounts
  - https://www.grantthornton.com/insights/newsletters/tax/2025/hot-topics/dec-23/draft-form-4547-further-clarifies-trump-account-framework
  - https://www.grantthornton.com/insights/newsletters/tax/2025/hot-topics/dec-09/irs-issues-initial-guidance-on-trump-accounts
  - https://www.mercer.com/insights/law-and-policy/employers-can-contribute-to-trump-accounts-starting-next-july/
  - https://www.paylocity.com/resources/tax-compliance/alerts/section-128-trump-account-contribution-program-requirements/
  - https://rsmus.com/insights/services/business-tax/trump-accounts-top-considerations-individuals-employers.html
  - https://www.nixonpeabody.com/insights/alerts/2026/03/11/trump-accounts-as-an-employee-benefit
  - https://www.savingforcollege.com/article/how-to-open-trump-account-irs-form-4547

Project references:
  - skill/reference/ty2025-landscape.md section 10a (OBBBA Form 4547)
  - skill/reference/ty2025-constants.json _todo entry (Trump Account limits)
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from skill.scripts.models import CanonicalReturn, Dependent

# ---------------------------------------------------------------------------
# TODO(constants): these literals are not yet in ty2025-constants.json (see
# the _todo list entry "Trump Account (Form 4547) contribution limits and
# eligibility"). A follow-up patch authorized to touch the constants JSON
# should add:
#
#   "new_obbba_forms.form_4547": {
#     "deduction_amount": 0,   # UNCHANGED per §219
#     "annual_contribution_cap": 5000,   # V3 above
#     "pilot_contribution_amount": 1000, # V2 above
#     "employer_section_128_cap": 2500,  # V4 above (for ingester, not here)
#     "years_applicable": [2025, 2026, 2027, 2028],
#     "pilot_birth_window_start": "2025-01-01",
#     "pilot_birth_window_end":   "2028-12-31",
#     "beneficiary_max_age": 18,
#     "_sources": [
#       "https://www.irs.gov/instructions/i4547",
#       "https://www.irs.gov/pub/irs-pdf/f4547.pdf",
#       "P.L. 119-21 Title VII"
#     ]
#   }
#
# Until then these literals live here with inline citations.
# ---------------------------------------------------------------------------

_DEDUCTION_AMOUNT: Decimal = Decimal("0")
# VERIFIED (V1) — IRC §219 disallowance, Form 4547 instructions (12/2025).

_ANNUAL_CONTRIBUTION_CAP: Decimal = Decimal("5000")
# VERIFIED (V3) — Form 4547 instructions (12/2025); statute §530A.
# Cost-of-living adjustment after 2027 is not yet indexed (no IRS Rev. Proc.
# because the first contribution date is 2026-07-04 — wait for TY2028
# inflation adjustment before indexing).

_PILOT_CONTRIBUTION_AMOUNT: Decimal = Decimal("1000")
# VERIFIED (V2) — Form 4547 Part III, IRS instructions (12/2025).

_EMPLOYER_128_CAP: Decimal = Decimal("2500")
# VERIFIED (V4) — Skadden, Paylocity, IRC §128(b)(1). NOT applied in this
# patch; left as a module-level constant so an ingester can use it later.

_PILOT_BIRTH_WINDOW_START: dt.date = dt.date(2025, 1, 1)
_PILOT_BIRTH_WINDOW_END: dt.date = dt.date(2028, 12, 31)
# VERIFIED (V5) — instructions quote "born after December 31, 2024, and
# before January 1, 2029".

_BENEFICIARY_MAX_AGE: int = 18
# VERIFIED — Form 4547 instructions: child must be "under age 18 at the
# end of the year in which the election was made".

# UNVERIFIED (U1) — pilot window proxy for years_applicable.
# LOCKED: pending final Form 4547 instructions per P.L. 119-21.
_YEARS_APPLICABLE: tuple[int, ...] = (2025, 2026, 2027, 2028)


@dataclass(frozen=True)
class TrumpAccountResult:
    """Result of the OBBBA Form 4547 Trump Account computation.

    Attributes
    ----------
    deduction : Decimal
        Dollar amount to subtract from AGI on Schedule 1 Part II
        ``trump_account_deduction_form_4547``. **Always $0** per IRC §219
        as clarified by Form 4547 instructions (12/2025). Kept as a named
        field so the caller can safely fold it into
        ``AdjustmentsToIncome.trump_account_deduction_form_4547`` without
        a conditional.
    num_qualifying_children : int
        Count of children on the return eligible for a Form 4547 Pilot
        Program Contribution election (born 2025-2028, under 18 at year
        end, marked as qualifying child).
    base_deduction : Decimal
        Equal to ``deduction`` (always $0). Kept for API consistency with
        SeniorDeductionResult / Schedule1AResult.
    phase_out_reduction : Decimal
        Always $0 — there is no AGI/MAGI phase-out on Trump Account
        eligibility in current IRS guidance. Kept for API consistency.
    details : dict
        Audit trail with tax_year, years_applicable, per-child eligibility
        reasons, annual cap, pilot contribution amount, and the loud
        warning that individual contributions are NOT deductible under
        §219.
    """

    deduction: Decimal
    num_qualifying_children: int
    base_deduction: Decimal
    phase_out_reduction: Decimal
    details: dict[str, Any] = field(default_factory=dict)


def _age_at_end_of_year(dob: dt.date, tax_year: int) -> int:
    """Age on 12/31 of the tax year. Matches CTC/senior patches."""
    end_of_year = dt.date(tax_year, 12, 31)
    years = end_of_year.year - dob.year
    if (end_of_year.month, end_of_year.day) < (dob.month, dob.day):
        years -= 1
    return years


def _is_in_pilot_birth_window(dob: dt.date) -> bool:
    """True if the child was born in the Trump Account pilot window.

    Per Form 4547 (12/2025) instructions: "born after December 31, 2024,
    and before January 1, 2029." We model this inclusively on 2025-01-01
    through 2028-12-31.
    """
    return _PILOT_BIRTH_WINDOW_START <= dob <= _PILOT_BIRTH_WINDOW_END


def _count_qualifying_children(
    dependents: list[Dependent],
    tax_year: int,
) -> int:
    """Count dependents eligible for a Form 4547 Trump Account election.

    Eligibility:
    - Must be marked ``is_qualifying_child`` on the return (proxy for the
      "anticipated qualifying child" language in Form 4547 instructions).
      UNVERIFIED (U2) — see module docstring.
    - Must be under ``_BENEFICIARY_MAX_AGE`` (18) at end of tax year.
    - Must be born inside the pilot birth window (2025-01-01 .. 2028-12-31)
      for the Pilot Program Contribution slice of Form 4547.

    A dependent not in the pilot window but under 18 can still have an
    account opened (Parts I/II of the form), but gets no $1,000 pilot.
    For the count returned here we use the stricter pilot-window test
    because the deduction amount is $0 in either case and the pilot is
    the more audit-relevant signal.
    """
    count = 0
    for dep in dependents:
        if not dep.is_qualifying_child:
            continue
        dob = dep.person.date_of_birth
        if not _is_in_pilot_birth_window(dob):
            continue
        age = _age_at_end_of_year(dob, tax_year)
        if age >= _BENEFICIARY_MAX_AGE:
            continue
        count += 1
    return count


def _is_year_gated(tax_year: int) -> bool:
    """True if the tax year is outside the OBBBA Form 4547 window."""
    return tax_year not in _YEARS_APPLICABLE


def compute_trump_account_deduction(
    return_: CanonicalReturn,
    magi: Decimal,
) -> TrumpAccountResult:
    """Compute the OBBBA Form 4547 Trump Account "deduction" for a canonical
    return.

    **The deduction is always $0** per IRC §219 as explicitly disallowed
    by Form 4547 instructions (12/2025). This function exists to:

    1. Populate ``AdjustmentsToIncome.trump_account_deduction_form_4547``
       with a well-defined zero, matching the other OBBBA patches' shape.
    2. Count and audit-log qualifying children for eventual Form 4547
       election rendering (when the skill's output layer produces the
       form PDF).
    3. Provide a single canonical place to update if final Treasury
       regulations (NPRM docket 2026-04533) ever add a deductible
       contribution path.

    Parameters
    ----------
    return_ : CanonicalReturn
        The canonical tax return. Supplies dependents, filing status, and
        tax_year for age calculation.
    magi : Decimal
        Modified AGI. Accepted for API consistency with sibling patches
        (CTC, senior deduction, Schedule 1-A). **Not currently used** —
        there is no income-based phase-out on the Trump Account election.
        Still stored in ``details["magi"]`` for audit trail.

    Returns
    -------
    TrumpAccountResult
        Frozen dataclass with the (always-zero) deduction, qualifying-
        child count for Pilot Program Contribution election, and an audit
        trail.
    """
    num_qualifiers = _count_qualifying_children(
        return_.dependents, return_.tax_year
    )

    # Year gate: before TY2025 or after TY2028 the form does not apply.
    # UNVERIFIED (U1) — locked to pilot window; update on final regs.
    if _is_year_gated(return_.tax_year):
        return TrumpAccountResult(
            deduction=Decimal("0"),
            num_qualifying_children=num_qualifiers,
            base_deduction=Decimal("0"),
            phase_out_reduction=Decimal("0"),
            details={
                "filing_status": return_.filing_status.value,
                "tax_year": return_.tax_year,
                "years_applicable": list(_YEARS_APPLICABLE),
                "year_gated": True,
                "magi": str(magi),
                "num_qualifying_children": num_qualifiers,
                "deduction_note": (
                    "Tax year outside the OBBBA Form 4547 window. "
                    "No election available."
                ),
                "UNVERIFIED_assumptions": [
                    "U1: years_applicable locked to 2025-2028 pending final IRS regulations",
                ],
            },
        )

    # Inside the OBBBA window: deduction is still $0 per §219, but the
    # election is available. Count qualifiers for audit + future form-
    # rendering pipeline.
    base_deduction = _DEDUCTION_AMOUNT
    deduction = base_deduction  # no phase-out, no adjustment — stays $0

    details: dict[str, Any] = {
        "filing_status": return_.filing_status.value,
        "tax_year": return_.tax_year,
        "years_applicable": list(_YEARS_APPLICABLE),
        "year_gated": False,
        "magi": str(magi),
        "num_qualifying_children": num_qualifiers,
        "annual_contribution_cap": str(_ANNUAL_CONTRIBUTION_CAP),
        "pilot_contribution_amount_per_child": str(_PILOT_CONTRIBUTION_AMOUNT),
        "pilot_contribution_total_eligible": str(
            _PILOT_CONTRIBUTION_AMOUNT * Decimal(num_qualifiers)
        ),
        "employer_128_cap_reference_only": str(_EMPLOYER_128_CAP),
        "beneficiary_max_age": _BENEFICIARY_MAX_AGE,
        "pilot_birth_window_start": _PILOT_BIRTH_WINDOW_START.isoformat(),
        "pilot_birth_window_end": _PILOT_BIRTH_WINDOW_END.isoformat(),
        "base_deduction": str(base_deduction),
        "phase_out_reduction": "0",
        "deduction": str(deduction),
        "deduction_note": (
            "LOUD WARNING: individual contributions to a Trump Account are "
            "NOT deductible under IRC §219 per Form 4547 instructions "
            "(12/2025). The `trump_account_deduction_form_4547` adjustment "
            "field on the canonical model will always be $0 for TY2025-2028 "
            "unless final Treasury regulations (NPRM 2026-04533) add a "
            "deductible path. This patch intentionally returns $0."
        ),
        "VERIFIED_assumptions": [
            "V1: $0 deduction per IRC §219 (IRS primary source confirmed)",
            "V2: $1,000 pilot program contribution per eligible child",
            "V3: $5,000 annual aggregate contribution cap per beneficiary",
            "V4: $2,500 employer §128 exclusion (NOT applied in this patch)",
            "V5: pilot birth window 2025-01-01 through 2028-12-31",
        ],
        "UNVERIFIED_assumptions": [
            "U1: years_applicable locked to 2025-2028 pending final IRS regs",
            "U2: qualifying child proxy may be looser than statutory "
            "'authorized individual' priority (parent/sibling/grandparent)",
            "U3: $0 deduction is permanent — re-verify on each IRS release",
            "U4: no AGI/MAGI phase-out in current IRS guidance",
        ],
        "todo_constants": (
            "Move literals into ty2025-constants.json new_obbba_forms.form_4547; "
            "see module docstring for the schema."
        ),
    }

    return TrumpAccountResult(
        deduction=deduction,
        num_qualifying_children=num_qualifiers,
        base_deduction=base_deduction,
        phase_out_reduction=Decimal("0"),
        details=details,
    )
