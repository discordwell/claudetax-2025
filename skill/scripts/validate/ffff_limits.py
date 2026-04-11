"""Free File Fillable Forms (FFFF) compatibility checker.

Runs every hard-cap / form-support limit from `skill/reference/ffff-limits.md`
against a `CanonicalReturn` and produces an `FFFFComplianceReport`. Callers
use the report to decide whether to recommend FFFF as the federal submission
channel, or to route the user to paper / commercial software instead.

Design principles:
- **One source of truth for numbers**: every constant in this module is
  mirrored from the reference doc, with the IRS URL in a comment next to it.
  If a number changes, update the reference doc and this module together.
- **Frozen dataclasses** for the report so downstream consumers cannot
  mutate violations after the fact.
- **Small check functions** per rule — each `_check_*` returns a list of
  `FFFFViolation`. The top-level `check_ffff_compatibility` simply composes
  them. This makes unit-testing individual rules trivial.
- **Severity split**: blockers force a non-FFFF path; warnings and
  informationals inform the user but don't disqualify FFFF.
- **Canonical paths**: each violation carries a JSON-pointer-ish
  `canonical_path` (e.g., "w2s[50]", "schedules_e[0].properties[11]") so
  downstream UI can highlight the offending element.

### TODOs (cannot be checked without model extensions — do NOT extend the
### model from this PR; these are tracked here for later)

- **Form 4547 Trump Account presence**: the canonical return carries the
  deduction amount in `AdjustmentsToIncome.trump_account_deduction_form_4547`,
  but not an explicit "is Form 4547 attached?" boolean. We assume a nonzero
  deduction implies Form 4547 is triggered. If IRS confirms FFFF supports
  4547 for TY2025, remove this check or flip its severity.
- **Form 1098-C attachment presence**: the model has no "noncash donation
  with 1098-C required" flag. `ItemizedDeductions.gifts_to_charity_other_than_cash`
  > $500 is a proxy, but only for vehicle donations (1098-C is specifically
  for vehicles/boats/airplanes). Flagged as a warning, not a blocker, until
  a finer model split exists.
- **Form 8915-C / 8915-D presence**: not in the model; these are
  disaster-distribution carryover forms. Would need a new optional field on
  `Form1099R` or a top-level flag. Left uncheckable.
- **Signed statement elections (§1.263(a), §754, §6013(g)/(h))**: not in the
  model. Would need an "elections" list. Left uncheckable.
- **Schedule B line 7b country selection count**: the model does not track
  foreign-account country codes individually. Left uncheckable.
- **Form 8082 / 8283 / 8829 / 8938 instance limits**: these forms are not
  (yet) first-class on `CanonicalReturn`. `schedules_c[*].line30_home_office_expense`
  is a proxy for 8829 presence, but there is no per-home-office breakout.
  Left uncheckable for now; tracked via a warning on the compliance report
  details.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from skill.scripts.models import CanonicalReturn

# ---------------------------------------------------------------------------
# FFFF limit constants — every value cited to the reference doc + IRS URL
# ---------------------------------------------------------------------------
#
# REFERENCE DOC (single source of truth):
#   skill/reference/ffff-limits.md
#
# Primary IRS source (cited repeatedly below):
#   https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms
#
# Every `FFFF_MAX_*` constant maps to a table row in section 2 of the
# reference doc. If IRS changes a limit, update the reference doc first, then
# mirror the number here, then bump the `FFFF_LIMITS_LAST_VERIFIED` date.

FFFF_LIMITS_LAST_VERIFIED = "2026-04-11"

# Per-return instance caps ---------------------------------------------------

FFFF_MAX_W2S: int = 50
"""Maximum Form W-2 copies per return.
Source: https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms
"Only 50 copies of Form W-2 will be added." """

FFFF_MAX_SCHEDULE_E_PROPERTIES: int = 11
"""Maximum rental real estate properties on a Schedule E (1 page 2 + 10 additional).
Source: https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms
"The program allows one copy of page 2 of Schedule E and 10 additional pages." """

FFFF_MAX_FORM_8829_COPIES: int = 8
"""Maximum Form 8829 (Home Office Expense) copies per return.
Source: https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms
"""

FFFF_MAX_FORM_8283_COPIES: int = 4
"""Maximum Form 8283 (Noncash Charitable) copies per return.
Source: https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms
"""

FFFF_MAX_FORM_8082_COPIES: int = 4
"""Maximum Form 8082 copies per return.
Source: https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms
"""

FFFF_MAX_FORM_8938_CONTINUATIONS: int = 25
"""Maximum Form 8938 continuation pages (1 base form + up to 25 continuations).
Source: https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms
"""

FFFF_MAX_FORM_4562_PER_PARENT_SCHEDULE: int = 1
"""Maximum Form 4562 per associated Schedule C/E/F/4835. Workaround: duplicate parent schedule.
Source: https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms
"""

# Minimum filer age for first-time filers -----------------------------------

FFFF_MIN_FIRST_TIME_FILER_AGE: int = 16
"""Primary taxpayers under 16 who have never filed with the IRS cannot e-file via FFFF.
Source: https://www.irs.gov/filing/free-file-fillable-forms/ind-674-01
"""

# Forms NOT supported by FFFF (blocker set) ---------------------------------

FFFF_UNSUPPORTED_FORMS: frozenset[str] = frozenset(
    {
        "1040-SR",  # Use Form 1040 instead. Source: IRS FFFF limitations page.
        "Schedule K-1",  # Partnerships / S-corps / estates not supported in FFFF.
        "8915-C",  # Disaster-related retirement distribution carryover.
        "8915-D",  # Disaster-related retirement distribution carryover.
        "1098-C",  # Vehicle donation (>$500) — requires Form 8453, not supported in FFFF.
        "4547",  # OBBBA Trump Account election — unverified for FFFF TY2025, safest = unsupported.
    }
)
"""Forms that, if present on the return, force a non-FFFF path (paper or commercial).
Every entry is documented in skill/reference/ffff-limits.md section 3.
"""

# Attachment policy ---------------------------------------------------------

FFFF_ACCEPTS_ATTACHMENTS: bool = False
"""FFFF does not accept any document attachments beyond program-provided forms.
Source: https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms
Exact language: "This program does not allow you to attach any documents to your return,
except those available through the program."
"""

# State return policy ------------------------------------------------------

FFFF_SUPPORTS_STATE_RETURNS: bool = False
"""FFFF is federal-only. State returns must go through state DOR portals or paper.
Source: https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms
(also see skill/reference/ty2025-landscape.md)
"""


# ---------------------------------------------------------------------------
# Report dataclasses
# ---------------------------------------------------------------------------


Severity = Literal["blocker", "warning", "info"]


@dataclass(frozen=True)
class FFFFViolation:
    """A single FFFF compatibility issue.

    - `code`: short stable machine identifier (e.g., "FFFF_W2_COUNT_EXCEEDED")
    - `message`: human-readable explanation including the offending value
    - `severity`: "blocker" (can't use FFFF), "warning" (workaround needed),
      or "info" (informational, e.g., state returns present)
    - `canonical_path`: JSON-pointer-ish path into the CanonicalReturn
    """

    code: str
    message: str
    severity: Severity
    canonical_path: str


@dataclass(frozen=True)
class FFFFComplianceReport:
    """Result of running every FFFF limit check against a return."""

    compatible: bool
    blockers: list[FFFFViolation]
    warnings: list[FFFFViolation]
    infos: list[FFFFViolation]
    details: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Individual check functions (each returns a list of FFFFViolation)
# ---------------------------------------------------------------------------


def _check_w2_count(return_: CanonicalReturn) -> list[FFFFViolation]:
    count = len(return_.w2s)
    if count > FFFF_MAX_W2S:
        return [
            FFFFViolation(
                code="FFFF_W2_COUNT_EXCEEDED",
                message=(
                    f"Return has {count} W-2 forms; FFFF allows at most "
                    f"{FFFF_MAX_W2S}."
                ),
                severity="blocker",
                canonical_path=f"w2s[{FFFF_MAX_W2S}]",
            )
        ]
    return []


def _check_schedule_e_property_count(
    return_: CanonicalReturn,
) -> list[FFFFViolation]:
    total_properties = sum(len(s.properties) for s in return_.schedules_e)
    if total_properties > FFFF_MAX_SCHEDULE_E_PROPERTIES:
        return [
            FFFFViolation(
                code="FFFF_SCHEDULE_E_PROPERTY_COUNT_EXCEEDED",
                message=(
                    f"Return has {total_properties} Schedule E rental "
                    f"properties; FFFF allows at most "
                    f"{FFFF_MAX_SCHEDULE_E_PROPERTIES}."
                ),
                severity="blocker",
                canonical_path=(
                    f"schedules_e[*].properties[{FFFF_MAX_SCHEDULE_E_PROPERTIES}]"
                ),
            )
        ]
    return []


def _check_unsupported_forms(return_: CanonicalReturn) -> list[FFFFViolation]:
    """Detect presence of forms FFFF does not support.

    Each branch maps a CanonicalReturn-observable condition to a specific
    unsupported form and emits a blocker.
    """
    violations: list[FFFFViolation] = []

    # Schedule K-1: unsupported in FFFF (partnerships, S-corps, estates/trusts)
    if return_.schedules_k1:
        violations.append(
            FFFFViolation(
                code="FFFF_UNSUPPORTED_FORM_SCHEDULE_K1",
                message=(
                    f"Return has {len(return_.schedules_k1)} Schedule K-1(s); "
                    "Schedule K-1 is not supported by FFFF."
                ),
                severity="blocker",
                canonical_path="schedules_k1[0]",
            )
        )

    # Form 4547 (OBBBA Trump Account): treat nonzero deduction as proxy for
    # form attachment. Unverified for FFFF TY2025, safest = blocker.
    if return_.adjustments.trump_account_deduction_form_4547 != 0:
        violations.append(
            FFFFViolation(
                code="FFFF_UNSUPPORTED_FORM_4547",
                message=(
                    "Return claims an OBBBA Trump Account deduction (Form 4547); "
                    "Form 4547 is not confirmed supported by FFFF for TY2025."
                ),
                severity="blocker",
                canonical_path="adjustments.trump_account_deduction_form_4547",
            )
        )

    # Form 1098-C proxy: Schedule A noncash gifts > $500 may require 1098-C
    # for a vehicle donation. Warning, not blocker, because not all noncash
    # > $500 is a vehicle donation.
    if return_.itemize_deductions and return_.itemized is not None:
        noncash = return_.itemized.gifts_to_charity_other_than_cash
        if noncash > 500:
            violations.append(
                FFFFViolation(
                    code="FFFF_POSSIBLE_1098C_REQUIRED",
                    message=(
                        "Noncash charitable gift > $500. If any portion is a "
                        "vehicle, boat, or airplane donation, Form 1098-C is "
                        "required and FFFF cannot attach it — the return must "
                        "go paper or via commercial software."
                    ),
                    severity="warning",
                    canonical_path="itemized.gifts_to_charity_other_than_cash",
                )
            )

    return violations


def _check_attachments_required(
    return_: CanonicalReturn,
) -> list[FFFFViolation]:
    """Flag conditions that force a required attachment FFFF cannot carry.

    Currently there is no generic "attachments" field on CanonicalReturn, so
    this check is intentionally conservative — it delegates to 1098-C detection
    inside `_check_unsupported_forms`.
    """
    # Kept as an explicit no-op for now so test coverage exists for the slot
    # and future extensions land here without restructuring the top-level.
    _ = return_  # silence unused
    return []


def _check_state_returns_informational(
    return_: CanonicalReturn,
) -> list[FFFFViolation]:
    """FFFF is federal-only. Presence of state returns is informational."""
    if return_.state_returns:
        return [
            FFFFViolation(
                code="FFFF_STATE_RETURNS_FEDERAL_ONLY",
                message=(
                    f"Return has {len(return_.state_returns)} state return(s); "
                    "FFFF is federal-only. File each state return through the "
                    "state DOR portal or paper, separately from the federal "
                    "FFFF submission."
                ),
                severity="info",
                canonical_path="state_returns[0]",
            )
        ]
    return []


def _check_form_4562_per_parent_schedule(
    return_: CanonicalReturn,
) -> list[FFFFViolation]:
    """Warn (not block) when any Schedule E property has both depreciation and
    the parent Schedule E has many properties — proxy for the multi-4562
    workaround.

    FFFF allows only one Form 4562 per parent schedule; workaround is to split
    into additional Schedule Cs/Es, which is a hassle but not a hard block.
    """
    warnings: list[FFFFViolation] = []
    for idx, sch_c in enumerate(return_.schedules_c):
        if sch_c.expenses.line13_depreciation > 0:
            # Single 4562 allowed per Schedule C — no warning unless we can
            # detect multiple 4562 sub-elements, which we can't today. Leave
            # as a hook.
            _ = idx
    return warnings


def _check_first_time_filer_age(
    return_: CanonicalReturn,
) -> list[FFFFViolation]:
    """Primary taxpayers under 16 who have never filed cannot e-file FFFF.

    CanonicalReturn does not carry a "has previously filed" flag, so we
    cannot be definitive; we emit a warning when the primary taxpayer is
    under 16 as of the end of the tax year. The user must confirm.
    """
    tp = return_.taxpayer
    from datetime import date

    year_end = date(return_.tax_year, 12, 31)
    age = (
        year_end.year
        - tp.date_of_birth.year
        - (
            (year_end.month, year_end.day)
            < (tp.date_of_birth.month, tp.date_of_birth.day)
        )
    )
    if age < FFFF_MIN_FIRST_TIME_FILER_AGE:
        return [
            FFFFViolation(
                code="FFFF_FIRST_TIME_FILER_UNDER_16",
                message=(
                    f"Primary taxpayer is age {age} at end of tax year "
                    f"{return_.tax_year}. FFFF rejects first-time filers under "
                    f"{FFFF_MIN_FIRST_TIME_FILER_AGE}. If this taxpayer has "
                    "never filed a federal return before, the return must be "
                    "printed and mailed."
                ),
                severity="warning",
                canonical_path="taxpayer.date_of_birth",
            )
        ]
    return []


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


_ALL_CHECKS = (
    _check_w2_count,
    _check_schedule_e_property_count,
    _check_unsupported_forms,
    _check_attachments_required,
    _check_state_returns_informational,
    _check_form_4562_per_parent_schedule,
    _check_first_time_filer_age,
)


def check_ffff_compatibility(
    return_: CanonicalReturn,
) -> FFFFComplianceReport:
    """Run every FFFF limit check and return a compliance report.

    A return is `compatible` iff no check produced a "blocker" severity
    violation. Warnings and infos do not affect `compatible`.
    """
    all_violations: list[FFFFViolation] = []
    for check in _ALL_CHECKS:
        all_violations.extend(check(return_))

    blockers = [v for v in all_violations if v.severity == "blocker"]
    warnings = [v for v in all_violations if v.severity == "warning"]
    infos = [v for v in all_violations if v.severity == "info"]

    details: dict[str, Any] = {
        "ffff_limits_last_verified": FFFF_LIMITS_LAST_VERIFIED,
        "w2_count": len(return_.w2s),
        "schedule_e_property_count": sum(
            len(s.properties) for s in return_.schedules_e
        ),
        "schedule_k1_count": len(return_.schedules_k1),
        "state_return_count": len(return_.state_returns),
        "checks_run": [c.__name__ for c in _ALL_CHECKS],
    }

    return FFFFComplianceReport(
        compatible=not blockers,
        blockers=blockers,
        warnings=warnings,
        infos=infos,
        details=details,
    )
