"""Structural tests for skill/SKILL.md and skill/reference/skill-interview-examples.md.

These tests are deliberately structural — they assert the SKILL.md interview
prompt has not drifted away from the canonical phase layout, that the
load-bearing CP8 references are still present, and that the worked-examples
document still covers the agreed taxpayer profiles. They are NOT semantic /
LLM-eval tests; that level of testing belongs to a separate wet-test pass.

The fixtures live two directories up from this test file:

    skill/SKILL.md
    skill/reference/skill-interview-examples.md
"""
from __future__ import annotations

from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

REPO_SKILL_DIR = Path(__file__).resolve().parent.parent
SKILL_MD_PATH = REPO_SKILL_DIR / "SKILL.md"
EXAMPLES_PATH = REPO_SKILL_DIR / "reference" / "skill-interview-examples.md"


@pytest.fixture(scope="module")
def skill_md_text() -> str:
    assert SKILL_MD_PATH.exists(), f"SKILL.md missing at {SKILL_MD_PATH}"
    return SKILL_MD_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def examples_md_text() -> str:
    assert EXAMPLES_PATH.exists(), f"examples doc missing at {EXAMPLES_PATH}"
    return EXAMPLES_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. SKILL.md exists and is non-trivial
# ---------------------------------------------------------------------------


def test_skill_md_exists():
    """SKILL.md must exist at the canonical path."""
    assert SKILL_MD_PATH.exists()
    assert SKILL_MD_PATH.is_file()


def test_skill_md_is_non_trivial(skill_md_text: str):
    """SKILL.md must be at least 3 KB — anything smaller is a stub."""
    size_bytes = len(skill_md_text.encode("utf-8"))
    assert size_bytes > 3 * 1024, (
        f"SKILL.md is only {size_bytes} bytes — looks like a stub. "
        "The interview prompt should be at least 3 KB."
    )


def test_skill_md_has_yaml_frontmatter(skill_md_text: str):
    """The skill manifest must start with a YAML frontmatter block."""
    assert skill_md_text.startswith("---\n"), "SKILL.md must open with YAML frontmatter"
    # frontmatter should declare a name and a description
    head = skill_md_text[: skill_md_text.find("\n---\n", 3) + 5]
    assert "name:" in head
    assert "description:" in head


# ---------------------------------------------------------------------------
# 2. Required interview phases
# ---------------------------------------------------------------------------


def _lower(text: str) -> str:
    return text.lower()


@pytest.mark.parametrize(
    "needle",
    [
        # filing status
        "filing status",
        # identity
        "identity",
        # income
        "income",
        # itemized vs standard
        "itemized",
        "standard deduction",
        # state residency
        "state residency",
        # OBBBA items
        "obbba",
        # FFFF compatibility
        "free file fillable forms",
        # pipeline handoff
        "pipeline",
    ],
)
def test_skill_md_contains_required_section_keyword(skill_md_text: str, needle: str):
    """SKILL.md must contain each required topic keyword (case-insensitive)."""
    assert needle in _lower(skill_md_text), (
        f"required keyword '{needle}' missing from SKILL.md — "
        "this is a structural drift; add the section back."
    )


def test_skill_md_has_phase_headers(skill_md_text: str):
    """SKILL.md must use the Phase 0..Phase 8 headers as anchors."""
    text = skill_md_text
    # at least these phase markers must appear in the document
    expected_phases = [
        "Phase 0",
        "Phase 1",
        "Phase 2",
        "Phase 3",
        "Phase 4",
        "Phase 5",
        "Phase 6",
        "Phase 7",
        "Phase 8",
    ]
    for phase in expected_phases:
        assert phase in text, f"phase header '{phase}' missing from SKILL.md"


# ---------------------------------------------------------------------------
# 3. Load-bearing CP8 references — these MUST stay in the prompt
# ---------------------------------------------------------------------------


def test_skill_md_references_cp8a_medical_floor(skill_md_text: str):
    """The CP8-A medical-floor warning must appear verbatim.

    This is load-bearing: without it, Claude does not warn the user about the
    7.5%-of-AGI floor on Schedule A medical, and tenforty over-deducts medical
    by the floor amount — a real-money correctness bug. The fix lives in
    skill/scripts/calc/engine.py and the interview must surface it.
    """
    text_lower = _lower(skill_md_text)
    assert "cp8-a" in text_lower, "SKILL.md must explicitly reference CP8-A"
    assert "7.5%" in skill_md_text, (
        "SKILL.md must mention the 7.5%-of-AGI medical floor in plain language"
    )
    assert "medical" in text_lower
    # The warning must mention adjusted gross income or AGI
    assert "agi" in text_lower or "adjusted gross income" in text_lower


def test_skill_md_references_cp8d_md_county(skill_md_text: str):
    """CP8-D added Address.county for the Maryland local-tax piggyback.

    The interview must collect county for MD residents or the MD plugin
    silently falls back to the 2.25% nonresident default and over-charges
    the taxpayer.
    """
    text_lower = _lower(skill_md_text)
    assert "cp8-d" in text_lower, "SKILL.md must explicitly reference CP8-D"
    assert "maryland" in text_lower or " md " in text_lower or "md resident" in text_lower
    assert "county" in text_lower, "SKILL.md must mention collecting county for MD"


def test_skill_md_references_pipeline_entry_point(skill_md_text: str):
    """The pipeline handoff must point at skill/scripts/pipeline.py::run_pipeline."""
    assert "skill/scripts/pipeline.py" in skill_md_text or (
        "skill.scripts.pipeline" in skill_md_text and "run_pipeline" in skill_md_text
    ), "SKILL.md must reference skill/scripts/pipeline.py and run_pipeline"
    assert "run_pipeline" in skill_md_text


def test_skill_md_references_canonical_return_model(skill_md_text: str):
    """SKILL.md must point at the CanonicalReturn target model."""
    assert "CanonicalReturn" in skill_md_text
    assert "models.py" in skill_md_text or "return.schema.json" in skill_md_text


# ---------------------------------------------------------------------------
# 4. OBBBA-specific item coverage (Phase 6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "needle",
    [
        "senior deduction",
        "schedule 1-a",
        "qualified tips",
        "qualified overtime",
        "form 4547",
        "trump account",
    ],
)
def test_skill_md_covers_obbba_items(skill_md_text: str, needle: str):
    """Each OBBBA-specific item must be surfaced in the interview."""
    assert needle in _lower(skill_md_text), (
        f"OBBBA item '{needle}' missing from SKILL.md — Phase 6 is incomplete"
    )


def test_skill_md_form_4547_is_zero_deduction(skill_md_text: str):
    """The interview must tell the user Form 4547 is a $0 deduction line."""
    text_lower = _lower(skill_md_text)
    assert "4547" in text_lower
    assert "$0" in skill_md_text or "0 " in skill_md_text or "not deductible" in text_lower


# ---------------------------------------------------------------------------
# 5. FFFF compatibility blockers (Phase 7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "needle",
    [
        "schedule k-1",
        "50 w-2",
        "11 schedule e",
        "1040-sr",
        "ffff-limits.md",
    ],
)
def test_skill_md_lists_ffff_blockers(skill_md_text: str, needle: str):
    """The FFFF blocker checklist must call out each named limit."""
    assert needle in _lower(skill_md_text), (
        f"FFFF blocker '{needle}' missing from SKILL.md — Phase 7 is incomplete"
    )


# ---------------------------------------------------------------------------
# 6. State residency / state plugin coverage
# ---------------------------------------------------------------------------


def test_skill_md_references_state_gap_doc(skill_md_text: str):
    """SKILL.md must point at the state coverage matrix."""
    assert "tenforty-ty2025-gap.md" in skill_md_text, (
        "SKILL.md must reference skill/reference/tenforty-ty2025-gap.md"
    )


def test_skill_md_mentions_no_income_tax_states(skill_md_text: str):
    """The state phase must mention the no-income-tax batch (8 states)."""
    text = skill_md_text
    # at least mention a few of the 8: AK FL NV NH SD TN TX WY
    no_tax_hits = sum(
        1
        for code in ("AK", "FL", "NV", "NH", "SD", "TN", "TX", "WY")
        if code in text
    )
    assert no_tax_hits >= 4, "SKILL.md should reference the no-income-tax states"


# ---------------------------------------------------------------------------
# 7. Income source sub-flow coverage (Phase 3)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "needle",
    [
        "w-2",
        "1099-int",
        "1099-div",
        "1099-b",
        "1099-nec",
        "1099-r",
        "ssa-1099",
        "schedule c",
        "schedule e",
        "schedule k-1",
    ],
)
def test_skill_md_covers_income_source(skill_md_text: str, needle: str):
    """Phase 3 must cover every income-source sub-flow."""
    assert needle in _lower(skill_md_text), (
        f"income source '{needle}' missing from SKILL.md — Phase 3 is incomplete"
    )


# ---------------------------------------------------------------------------
# 8. Worked-examples doc structure
# ---------------------------------------------------------------------------


def test_examples_md_exists():
    """The examples doc must exist next to the other reference material."""
    assert EXAMPLES_PATH.exists()
    assert EXAMPLES_PATH.is_file()


def test_examples_md_is_non_trivial(examples_md_text: str):
    """Examples doc must be at least 2 KB."""
    size_bytes = len(examples_md_text.encode("utf-8"))
    assert size_bytes > 2 * 1024, (
        f"examples doc is only {size_bytes} bytes — too thin to be useful"
    )


def test_examples_md_has_at_least_two_examples(examples_md_text: str):
    """At least two worked examples must be present."""
    # Examples are headed "## Example N — ..." — count those headers
    example_header_count = sum(
        1 for line in examples_md_text.splitlines() if line.startswith("## Example ")
    )
    assert example_header_count >= 2, (
        f"only {example_header_count} worked example(s) found — need at least 2"
    )


def test_examples_md_covers_simple_w2_and_complex_profile(examples_md_text: str):
    """At least one example must be the simple W-2 path AND at least one must
    exercise self-employment, itemized, or a complex profile."""
    text_lower = _lower(examples_md_text)
    # simple W-2 happy-path coverage
    assert "w-2" in text_lower
    # complexity coverage: self-employment OR itemized OR Schedule C / E / K-1
    has_complexity = any(
        kw in text_lower
        for kw in (
            "schedule c",
            "schedule e",
            "schedule k-1",
            "itemized",
            "self-employ",
            "freelance",
        )
    )
    assert has_complexity, (
        "examples doc must include at least one non-trivial example "
        "(self-employment, itemized, or schedule C/E/K-1)"
    )


def test_examples_md_references_cp8_fixes(examples_md_text: str):
    """Examples should walk the user through the CP8-A and CP8-D code paths
    so we can see the warnings firing in context."""
    text_lower = _lower(examples_md_text)
    # CP8-A medical floor must show up
    assert "7.5%" in examples_md_text or "cp8-a" in text_lower
    # CP8-D MD county must show up
    assert "county" in text_lower
    assert "maryland" in text_lower or " md " in text_lower
