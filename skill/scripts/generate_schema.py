"""Regenerate skill/schemas/return.schema.json from the Pydantic models.

Run from repo root:

    .venv/bin/python -m skill.scripts.generate_schema

Tests verify the committed schema matches what this generates — so after any
change to models.py, re-run this and commit the updated return.schema.json.
"""
from __future__ import annotations

import json
from pathlib import Path

from skill.scripts.models import CanonicalReturn

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "return.schema.json"


def generate() -> dict:
    schema = CanonicalReturn.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://tax-prep-skill/schemas/return.schema.json"
    schema["title"] = "CanonicalReturn"
    return schema


def write() -> Path:
    schema = generate()
    SCHEMA_PATH.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n")
    return SCHEMA_PATH


if __name__ == "__main__":
    path = write()
    print(f"wrote {path}")
