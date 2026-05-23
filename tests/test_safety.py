# tests/test_safety.py
# Unit tests for nowlink/safety.py
# Tests cover: field validation, diff generation, and write log creation.
# No HTTP calls — all pure logic tests.

import json
import pytest
from pathlib import Path
from nowlink.safety import validate_fields, diff_fields, log_write


# ── Fixtures ──────────────────────────────────────────────────────────────────

def schema(*mandatory_fields, optional=None):
    """
    Build a minimal table schema list as returned by shaper.shape_table_schema().
    Mandatory fields are passed as positional args (field name strings).
    Optional fields are passed as a list via the optional keyword.
    """
    result = []
    for name in mandatory_fields:
        result.append({"field": name, "label": name.replace("_", " ").title(), "type": "string", "mandatory": True})
    for name in (optional or []):
        result.append({"field": name, "label": name.replace("_", " ").title(), "type": "string"})
    return result


# ── validate_fields ───────────────────────────────────────────────────────────

class TestValidateFields:

    def test_all_mandatory_fields_present(self):
        """No errors when all mandatory fields are provided."""
        errors = validate_fields(
            {"short_description": "test", "caller_id": "john.doe"},
            schema("short_description", "caller_id"),
        )
        assert errors == []

    def test_missing_mandatory_field(self):
        """Returns an error for each missing mandatory field."""
        errors = validate_fields(
            {"short_description": "test"},
            schema("short_description", "caller_id"),
        )
        assert len(errors) == 1
        assert "caller_id" in errors[0]

    def test_all_mandatory_fields_missing(self):
        """Returns one error per mandatory field when none are provided."""
        errors = validate_fields(
            {},
            schema("short_description", "caller_id"),
        )
        assert len(errors) == 2

    def test_empty_string_counts_as_missing(self):
        """A blank string is treated the same as not providing the field."""
        errors = validate_fields(
            {"short_description": "   "},
            schema("short_description"),
        )
        assert len(errors) == 1

    def test_optional_fields_not_required(self):
        """Optional fields (no mandatory=True) are never flagged as errors."""
        errors = validate_fields(
            {},
            schema(optional=["description", "category"]),
        )
        assert errors == []

    def test_extra_fields_in_proposed_are_ignored(self):
        """Fields in proposed that don't appear in schema don't cause errors."""
        errors = validate_fields(
            {"short_description": "test", "mystery_field": "value"},
            schema("short_description"),
        )
        assert errors == []

    def test_empty_schema_no_errors(self):
        """An empty schema (no mandatory fields defined) produces no errors."""
        errors = validate_fields({"short_description": "test"}, [])
        assert errors == []


# ── diff_fields ───────────────────────────────────────────────────────────────

class TestDiffFields:

    def test_single_field_change(self):
        """A changed field appears in changes with from/to values."""
        current = {"priority": "P3 - Moderate", "state": "New"}
        proposed = {"priority": "1"}
        result = diff_fields(current, proposed)
        assert len(result["changes"]) == 1
        assert result["changes"][0]["field"] == "priority"
        assert result["changes"][0]["from"] == "P3 - Moderate"
        assert result["changes"][0]["to"] == "1"
        assert result["unchanged"] == []
        assert result["new_fields"] == []

    def test_unchanged_field(self):
        """A field where old and new values match goes into unchanged."""
        current = {"priority": "1"}
        proposed = {"priority": "1"}
        result = diff_fields(current, proposed)
        assert result["changes"] == []
        assert "priority" in result["unchanged"]

    def test_new_field_not_in_current(self):
        """A field being set that doesn't exist on the current record goes into new_fields."""
        current = {"short_description": "test"}
        proposed = {"close_notes": "fixed"}
        result = diff_fields(current, proposed)
        assert result["changes"] == []
        assert len(result["new_fields"]) == 1
        assert result["new_fields"][0]["field"] == "close_notes"

    def test_multiple_changes(self):
        """Multiple changed fields all appear in changes."""
        current = {"priority": "P3 - Moderate", "state": "New", "assigned_to": "John"}
        proposed = {"priority": "1", "state": "2", "assigned_to": "Jane"}
        result = diff_fields(current, proposed)
        assert len(result["changes"]) == 3

    def test_empty_proposed(self):
        """No proposed changes means empty diff."""
        current = {"priority": "P3 - Moderate"}
        result = diff_fields(current, {})
        assert result["changes"] == []
        assert result["unchanged"] == []
        assert result["new_fields"] == []

    def test_empty_current(self):
        """All proposed fields go into new_fields when current record is empty."""
        result = diff_fields({}, {"short_description": "test", "priority": "1"})
        assert result["changes"] == []
        assert len(result["new_fields"]) == 2


# ── log_write ─────────────────────────────────────────────────────────────────

class TestLogWrite:

    def test_create_log_entry(self, tmp_path, monkeypatch):
        """A create operation produces a valid JSON log entry."""
        import nowlink.safety as safety_module
        monkeypatch.setattr(safety_module, "WRITE_LOG_DIR", tmp_path)

        log_write("create", "incident", "INC0099999", {"short_description": "test"})

        log_files = list(tmp_path.glob("writes-*.log"))
        assert len(log_files) == 1

        lines = log_files[0].read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1

        entry = json.loads(lines[0])
        assert entry["op"] == "create"
        assert entry["table"] == "incident"
        assert entry["record"] == "INC0099999"
        assert entry["fields"]["short_description"] == "test"
        assert "ts" in entry
        assert "changes" not in entry  # no diff for creates

    def test_update_log_entry_includes_changes(self, tmp_path, monkeypatch):
        """An update operation includes the diff changes in the log entry."""
        import nowlink.safety as safety_module
        monkeypatch.setattr(safety_module, "WRITE_LOG_DIR", tmp_path)

        diff = {"changes": [{"field": "priority", "from": "P3 - Moderate", "to": "1"}], "unchanged": [], "new_fields": []}
        log_write("update", "incident", "INC0000055", {"priority": "1"}, diff=diff)

        log_files = list(tmp_path.glob("writes-*.log"))
        lines = log_files[0].read_text(encoding="utf-8").strip().splitlines()
        entry = json.loads(lines[0])

        assert entry["op"] == "update"
        assert len(entry["changes"]) == 1
        assert entry["changes"][0]["field"] == "priority"

    def test_multiple_writes_append(self, tmp_path, monkeypatch):
        """Multiple writes in the same day all go to the same log file, one per line."""
        import nowlink.safety as safety_module
        monkeypatch.setattr(safety_module, "WRITE_LOG_DIR", tmp_path)

        log_write("create", "incident", "INC0000001", {"short_description": "first"})
        log_write("update", "incident", "INC0000001", {"priority": "1"})

        log_files = list(tmp_path.glob("writes-*.log"))
        assert len(log_files) == 1
        lines = log_files[0].read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
