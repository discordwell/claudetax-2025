"""Pytest shared fixtures for the tax prep skill."""
from __future__ import annotations

from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = SKILL_DIR.parent


@pytest.fixture(scope="session")
def skill_dir() -> Path:
    """Absolute path to the skill/ directory."""
    return SKILL_DIR


@pytest.fixture(scope="session")
def repo_dir() -> Path:
    """Absolute path to the repo root."""
    return REPO_DIR


@pytest.fixture(scope="session")
def reference_dir(skill_dir: Path) -> Path:
    """Absolute path to skill/reference/."""
    return skill_dir / "reference"


@pytest.fixture(scope="session")
def fixtures_dir(skill_dir: Path) -> Path:
    """Absolute path to skill/fixtures/."""
    return skill_dir / "fixtures"


@pytest.fixture(scope="session")
def schemas_dir(skill_dir: Path) -> Path:
    """Absolute path to skill/schemas/."""
    return skill_dir / "schemas"
