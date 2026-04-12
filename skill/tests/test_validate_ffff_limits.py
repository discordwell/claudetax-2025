"""Tests for the Free File Fillable Forms (FFFF) compatibility checker.

Every numeric limit in `skill/scripts/validate/ffff_limits.py` is locked here
with an explicit test so that changes to the IRS-published limits must be
mirrored in both the reference doc and the checker before these tests pass.

Source of truth for the numbers: skill/reference/ffff-limits.md
Primary IRS URL:
  https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms
"""
from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal
from pathlib import Path

import pytest

from skill.scripts.models import (
    Address,
    AdjustmentsToIncome,
    CanonicalReturn,
    FilingStatus,
    ItemizedDeductions,
    Person,
    ResidencyStatus,
    ScheduleE,
    ScheduleEProperty,
    ScheduleK1,
    StateReturn,
    W2,
)
from skill.scripts.validate.ffff_limits import (
    FFFF_ACCEPTS_ATTACHMENTS,
    FFFF_MAX_FORM_8082_COPIES,
    FFFF_MAX_FORM_8283_COPIES,
    FFFF_MAX_FORM_8829_COPIES,
    FFFF_MAX_FORM_8938_CONTINUATIONS,
    FFFF_MAX_SCHEDULE_E_PROPERTIES,
    FFFF_MAX_W2S,
    FFFF_MIN_FIRST_TIME_FILER_AGE,
    FFFF_SUPPORTS_STATE_RETURNS,
    FFFF_UNSUPPORTED_FORMS,
    FFFFComplianceReport,
    FFFFViolation,
    check_ffff_compatibility,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _addr() -> Address:
    return Address(
        street1="1 Test Lane",
        city="Springfield",
        state="IL",
        zip="62701",
    )


def _person(
    first: str = "Alex",
    last: str = "Doe",
    ssn: str = "111-22-3333",
    dob: dt.date = dt.date(1990, 1, 15),
) -> Person:
    return Person(first_name=first, last_name=last, ssn=ssn, date_of_birth=dob)


def _w2(wages: Decimal = Decimal("65000")) -> W2:
    return W2(
        employer_name="Acme",
        employer_ein="12-3456789",
        box1_wages=wages,
    )


def _property() -> ScheduleEProperty:
    return ScheduleEProperty(
        address=_addr(),
        rents_received=Decimal("15000"),
    )


def _make_return(
    *,
    w2_count: int = 1,
    schedule_e_properties: int = 0,
    schedules_k1: int | None = None,
    state_returns: list[StateReturn] | None = None,
    taxpayer_dob: dt.date = dt.date(1990, 1, 15),
    adjustments: AdjustmentsToIncome | None = None,
    itemized: ItemizedDeductions | None = None,
) -> CanonicalReturn:
    schedules_e: list[ScheduleE] = []
    if schedule_e_properties > 0:
        schedules_e = [
            ScheduleE(properties=[_property() for _ in range(schedule_e_properties)])
        ]

    k1s: list[ScheduleK1] = []
    if schedules_k1:
        k1s = [
            ScheduleK1(source_name=f"Partnership {i}") for i in range(schedules_k1)
        ]

    kwargs: dict = {
        "tax_year": 2025,
        "filing_status": FilingStatus.SINGLE,
        "taxpayer": _person(dob=taxpayer_dob),
        "address": _addr(),
        "w2s": [_w2() for _ in range(w2_count)],
        "schedules_e": schedules_e,
        "schedules_k1": k1s,
        "state_returns": state_returns or [],
    }
    if adjustments is not None:
        kwargs["adjustments"] = adjustments
    if itemized is not None:
        kwargs["itemize_deductions"] = True
        kwargs["itemized"] = itemized
    return CanonicalReturn(**kwargs)


# ---------------------------------------------------------------------------
# Constant-locking tests — make the limits impossible to change silently
# ---------------------------------------------------------------------------


def test_constant_ffff_max_w2s_is_50() -> None:
    """IRS FFFF limitations page: 'Only 50 copies of Form W-2 will be added.'

    Source: https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms
    """
    assert FFFF_MAX_W2S == 50


def test_constant_ffff_max_schedule_e_properties_is_11() -> None:
    """Page 2 + 10 additional pages = 11 properties total.

    Source: https://www.irs.gov/e-file-providers/free-file-fillable-forms-program-limitations-and-available-forms
    """
    assert FFFF_MAX_SCHEDULE_E_PROPERTIES == 11


def test_constant_ffff_max_form_8829_is_8() -> None:
    assert FFFF_MAX_FORM_8829_COPIES == 8


def test_constant_ffff_max_form_8283_is_4() -> None:
    assert FFFF_MAX_FORM_8283_COPIES == 4


def test_constant_ffff_max_form_8082_is_4() -> None:
    assert FFFF_MAX_FORM_8082_COPIES == 4


def test_constant_ffff_max_form_8938_continuations_is_25() -> None:
    assert FFFF_MAX_FORM_8938_CONTINUATIONS == 25


def test_constant_ffff_first_time_filer_min_age_is_16() -> None:
    """FFFF rejects first-time filers under 16.

    Source: https://www.irs.gov/filing/free-file-fillable-forms/ind-674-01
    """
    assert FFFF_MIN_FIRST_TIME_FILER_AGE == 16


def test_constant_ffff_does_not_accept_attachments() -> None:
    assert FFFF_ACCEPTS_ATTACHMENTS is False


def test_constant_ffff_does_not_support_state_returns() -> None:
    assert FFFF_SUPPORTS_STATE_RETURNS is False


def test_constant_unsupported_forms_includes_k1_and_4547_and_1098c() -> None:
    assert "Schedule K-1" in FFFF_UNSUPPORTED_FORMS
    assert "4547" in FFFF_UNSUPPORTED_FORMS
    assert "1098-C" in FFFF_UNSUPPORTED_FORMS
    assert "1040-SR" in FFFF_UNSUPPORTED_FORMS
    assert "8915-C" in FFFF_UNSUPPORTED_FORMS
    assert "8915-D" in FFFF_UNSUPPORTED_FORMS


# ---------------------------------------------------------------------------
# Happy path: simple_w2_standard fixture passes all checks
# ---------------------------------------------------------------------------


def test_simple_w2_passes_all_checks(fixtures_dir: Path) -> None:
    """The canonical simple_w2_standard fixture must be FFFF-compatible."""
    data = json.loads((fixtures_dir / "simple_w2_standard" / "input.json").read_text())
    ret = CanonicalReturn.model_validate(data)
    report = check_ffff_compatibility(ret)

    assert isinstance(report, FFFFComplianceReport)
    assert report.compatible is True
    assert report.blockers == []
    # No state returns in the simple fixture, so no informationals either.
    assert report.infos == []
    assert report.details["w2_count"] == 1
    assert report.details["schedule_e_property_count"] == 0


# ---------------------------------------------------------------------------
# W-2 count checks
# ---------------------------------------------------------------------------


def test_exactly_at_w2_limit_is_ok() -> None:
    """A return with exactly 50 W-2s should pass — the limit is inclusive."""
    ret = _make_return(w2_count=FFFF_MAX_W2S)
    report = check_ffff_compatibility(ret)
    assert report.compatible is True
    assert not any(v.code == "FFFF_W2_COUNT_EXCEEDED" for v in report.blockers)


def test_too_many_w2s_is_blocker() -> None:
    """51 W-2s must produce a blocker citing the 50-limit."""
    ret = _make_return(w2_count=FFFF_MAX_W2S + 1)
    report = check_ffff_compatibility(ret)

    assert report.compatible is False
    w2_blockers = [v for v in report.blockers if v.code == "FFFF_W2_COUNT_EXCEEDED"]
    assert len(w2_blockers) == 1
    assert w2_blockers[0].severity == "blocker"
    assert "51" in w2_blockers[0].message
    assert "50" in w2_blockers[0].message
    assert w2_blockers[0].canonical_path.startswith("w2s[")


# ---------------------------------------------------------------------------
# Schedule E property count checks
# ---------------------------------------------------------------------------


def test_exactly_at_schedule_e_limit_is_ok() -> None:
    """11 rental properties should pass — the limit is inclusive."""
    ret = _make_return(schedule_e_properties=FFFF_MAX_SCHEDULE_E_PROPERTIES)
    report = check_ffff_compatibility(ret)
    assert report.compatible is True
    assert not any(
        v.code == "FFFF_SCHEDULE_E_PROPERTY_COUNT_EXCEEDED" for v in report.blockers
    )


def test_too_many_schedule_e_properties_is_blocker() -> None:
    """12 rental properties must produce a blocker citing the 11-limit."""
    ret = _make_return(schedule_e_properties=FFFF_MAX_SCHEDULE_E_PROPERTIES + 1)
    report = check_ffff_compatibility(ret)

    assert report.compatible is False
    sch_e_blockers = [
        v
        for v in report.blockers
        if v.code == "FFFF_SCHEDULE_E_PROPERTY_COUNT_EXCEEDED"
    ]
    assert len(sch_e_blockers) == 1
    assert sch_e_blockers[0].severity == "blocker"
    assert "12" in sch_e_blockers[0].message
    assert "11" in sch_e_blockers[0].message


# ---------------------------------------------------------------------------
# Unsupported forms
# ---------------------------------------------------------------------------


def test_schedule_k1_present_is_blocker() -> None:
    """Any Schedule K-1 makes the return FFFF-incompatible."""
    ret = _make_return(schedules_k1=1)
    report = check_ffff_compatibility(ret)

    assert report.compatible is False
    k1_blockers = [
        v for v in report.blockers if v.code == "FFFF_UNSUPPORTED_FORM_SCHEDULE_K1"
    ]
    assert len(k1_blockers) == 1
    assert k1_blockers[0].severity == "blocker"
    assert "K-1" in k1_blockers[0].message


def test_form_4547_trump_account_deduction_is_blocker() -> None:
    """Nonzero OBBBA Trump Account deduction triggers a Form 4547 blocker."""
    adj = AdjustmentsToIncome(
        trump_account_deduction_form_4547=Decimal("1000"),
    )
    ret = _make_return(adjustments=adj)
    report = check_ffff_compatibility(ret)

    assert report.compatible is False
    assert any(
        v.code == "FFFF_UNSUPPORTED_FORM_4547" for v in report.blockers
    ), "expected a Form 4547 blocker"


def test_noncash_charitable_over_500_is_warning_not_blocker() -> None:
    """Noncash gift > $500 is a 1098-C *warning*, not a hard blocker."""
    itemized = ItemizedDeductions(
        gifts_to_charity_other_than_cash=Decimal("750"),
    )
    ret = _make_return(itemized=itemized)
    report = check_ffff_compatibility(ret)

    # Still compatible because it's a warning, not a blocker
    assert report.compatible is True
    warning_codes = {v.code for v in report.warnings}
    assert "FFFF_POSSIBLE_1098C_REQUIRED" in warning_codes


# ---------------------------------------------------------------------------
# State returns: informational only
# ---------------------------------------------------------------------------


def test_state_returns_present_is_informational() -> None:
    """State returns must not block FFFF federal compatibility."""
    state_return = StateReturn(
        state="IL",
        residency=ResidencyStatus.RESIDENT,
        days_in_state=365,
        state_specific={"state_total_tax": 0},
    )
    ret = _make_return(state_returns=[state_return])
    report = check_ffff_compatibility(ret)

    # Informational only — FFFF is still fine for the federal return.
    assert report.compatible is True
    assert report.blockers == []
    info_codes = {v.code for v in report.infos}
    assert "FFFF_STATE_RETURNS_FEDERAL_ONLY" in info_codes
    assert report.infos[0].severity == "info"


# ---------------------------------------------------------------------------
# First-time filer under 16
# ---------------------------------------------------------------------------


def test_first_time_filer_under_16_is_warning() -> None:
    """Primary taxpayer under 16 produces a warning (cannot confirm first-time status)."""
    ret = _make_return(taxpayer_dob=dt.date(2012, 5, 1))  # age 13 at EOY 2025
    report = check_ffff_compatibility(ret)

    warning_codes = {v.code for v in report.warnings}
    assert "FFFF_FIRST_TIME_FILER_UNDER_16" in warning_codes
    # Warning, not blocker — we cannot confirm first-time status from the model.
    assert report.compatible is True


# ---------------------------------------------------------------------------
# Severity split / combined scenarios
# ---------------------------------------------------------------------------


def test_compliance_report_severity_split() -> None:
    """A return with blockers + warnings + infos must partition correctly."""
    adj = AdjustmentsToIncome(
        trump_account_deduction_form_4547=Decimal("500"),
    )
    itemized = ItemizedDeductions(
        gifts_to_charity_other_than_cash=Decimal("1000"),
    )
    state_return = StateReturn(
        state="IL",
        residency=ResidencyStatus.RESIDENT,
        days_in_state=365,
        state_specific={"state_total_tax": 0},
    )
    ret = _make_return(
        w2_count=FFFF_MAX_W2S + 1,  # blocker
        schedules_k1=2,  # blocker
        adjustments=adj,  # blocker (4547)
        itemized=itemized,  # warning (1098-C)
        state_returns=[state_return],  # info
    )
    report = check_ffff_compatibility(ret)

    # Incompatible because blockers exist
    assert report.compatible is False

    # Every list is properly partitioned by severity
    assert all(v.severity == "blocker" for v in report.blockers)
    assert all(v.severity == "warning" for v in report.warnings)
    assert all(v.severity == "info" for v in report.infos)

    # At least the expected codes show up
    blocker_codes = {v.code for v in report.blockers}
    warning_codes = {v.code for v in report.warnings}
    info_codes = {v.code for v in report.infos}

    assert "FFFF_W2_COUNT_EXCEEDED" in blocker_codes
    assert "FFFF_UNSUPPORTED_FORM_SCHEDULE_K1" in blocker_codes
    assert "FFFF_UNSUPPORTED_FORM_4547" in blocker_codes
    assert "FFFF_POSSIBLE_1098C_REQUIRED" in warning_codes
    assert "FFFF_STATE_RETURNS_FEDERAL_ONLY" in info_codes


def test_report_details_summarize_inputs() -> None:
    """details{} should always include basic counts and the last-verified date."""
    ret = _make_return(w2_count=3, schedule_e_properties=2, schedules_k1=0)
    report = check_ffff_compatibility(ret)

    assert report.details["w2_count"] == 3
    assert report.details["schedule_e_property_count"] == 2
    assert report.details["schedule_k1_count"] == 0
    assert report.details["state_return_count"] == 0
    assert "ffff_limits_last_verified" in report.details
    assert "checks_run" in report.details
    assert len(report.details["checks_run"]) >= 5


# ---------------------------------------------------------------------------
# Violation dataclass is frozen
# ---------------------------------------------------------------------------


def test_violation_is_frozen_dataclass() -> None:
    v = FFFFViolation(
        code="X",
        message="Y",
        severity="blocker",
        canonical_path="a.b[0]",
    )
    with pytest.raises(Exception):
        v.code = "Z"  # type: ignore[misc]
