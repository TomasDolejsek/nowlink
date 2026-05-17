# tests/test_shaper.py
# Unit tests for shaper.py — the core data transformation module.
# Run with: pytest tests/test_shaper.py -v

import pytest
from nowlink.shaper import (
    shape_record,
    shape_records,
    shape_table_schema,
    INCIDENT_STATE_MAP,
    PRIORITY_MAP,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def dv(display: str, value: str) -> dict:
    """Simulate a ServiceNow display_value=all field dict."""
    return {"display_value": display, "value": value}


# ── Priority mapping ──────────────────────────────────────────────────────────

def test_priority_p1():
    raw = {"number": dv("INC001", "INC001"), "priority": dv("1 - Critical", "1")}
    result = shape_record(raw, "incident")
    assert result["priority"] == "P1 - Critical"


def test_priority_p2():
    raw = {"number": dv("INC001", "INC001"), "priority": dv("2 - High", "2")}
    result = shape_record(raw, "incident")
    assert result["priority"] == "P2 - High"


def test_priority_p4():
    raw = {"number": dv("INC001", "INC001"), "priority": dv("4 - Low", "4")}
    result = shape_record(raw, "incident")
    assert result["priority"] == "P4 - Low"


# ── State mapping ─────────────────────────────────────────────────────────────

def test_incident_state_new():
    raw = {"number": dv("INC001", "INC001"), "state": dv("New", "1")}
    result = shape_record(raw, "incident")
    assert result["state"] == "New"


def test_incident_state_resolved():
    raw = {"number": dv("INC001", "INC001"), "state": dv("Resolved", "6")}
    result = shape_record(raw, "incident")
    assert result["state"] == "Resolved"


def test_problem_state():
    raw = {"number": dv("PRB001", "PRB001"), "state": dv("Root Cause Analysis", "103")}
    result = shape_record(raw, "problem")
    assert result["state"] == "Root Cause Analysis"


def test_change_state():
    raw = {"number": dv("CHG001", "CHG001"), "state": dv("Implement", "-1")}
    result = shape_record(raw, "change_request")
    assert result["state"] == "Implement"


# ── Reference field resolution ────────────────────────────────────────────────

def test_reference_field_resolves_to_display_name():
    raw = {
        "number": dv("INC001", "INC001"),
        "assigned_to": dv("John Smith", "abc123def456abc123def456abc123de"),
    }
    result = shape_record(raw, "incident")
    assert result["assigned_to"] == "John Smith"


def test_empty_reference_field_excluded():
    raw = {
        "number": dv("INC001", "INC001"),
        "assigned_to": dv("", ""),
    }
    result = shape_record(raw, "incident")
    assert "assigned_to" not in result


# ── System field stripping ────────────────────────────────────────────────────

def test_sys_mod_count_stripped():
    raw = {
        "number": dv("INC001", "INC001"),
        "sys_mod_count": dv("5", "5"),
        "sys_class_name": dv("Incident", "incident"),
    }
    result = shape_record(raw, "incident")
    assert "sys_mod_count" not in result
    assert "sys_class_name" not in result


# ── Allowlist filtering ───────────────────────────────────────────────────────

def test_only_allowlisted_fields_returned_for_incident():
    raw = {
        "number": dv("INC001", "INC001"),
        "priority": dv("1 - Critical", "1"),
        "totally_random_field": dv("foo", "foo"),
    }
    result = shape_record(raw, "incident")
    assert "totally_random_field" not in result
    assert "number" in result


def test_unknown_table_returns_all_non_stripped_fields():
    raw = {
        "number": dv("TST001", "TST001"),
        "some_custom_field": dv("hello", "hello"),
        "sys_mod_count": dv("3", "3"),
    }
    result = shape_record(raw, "custom_table_xyz")
    assert "some_custom_field" in result
    assert "sys_mod_count" not in result


# ── shape_records (list) ──────────────────────────────────────────────────────

def test_shape_records_returns_list():
    raw_list = [
        {"number": dv("INC001", "INC001"), "priority": dv("1 - Critical", "1")},
        {"number": dv("INC002", "INC002"), "priority": dv("2 - High", "2")},
    ]
    result = shape_records(raw_list, "incident")
    assert len(result) == 2
    assert result[0]["priority"] == "P1 - Critical"
    assert result[1]["priority"] == "P2 - High"


def test_shape_records_empty_list():
    result = shape_records([], "incident")
    assert result == []


# ── shape_table_schema ────────────────────────────────────────────────────────

def test_schema_mandatory_first():
    raw = [
        {"element": "short_description", "column_label": "Short description", "internal_type": "string",
         "mandatory": "true", "reference": ""},
        {"element": "category", "column_label": "Category", "internal_type": "string", "mandatory": "false",
         "reference": ""},
        {"element": "caller_id", "column_label": "Caller", "internal_type": "reference", "mandatory": "true",
         "reference": "sys_user"},
    ]
    result = shape_table_schema(raw)
    mandatory = [f for f in result if f.get("mandatory")]
    non_mandatory = [f for f in result if not f.get("mandatory")]
    # All mandatory fields appear before non-mandatory
    if mandatory and non_mandatory:
        last_mandatory_idx = max(result.index(f) for f in mandatory)
        first_non_mandatory_idx = min(result.index(f) for f in non_mandatory)
        assert last_mandatory_idx < first_non_mandatory_idx


def test_schema_reference_field_includes_references_key():
    raw = [
        {"element": "caller_id", "column_label": "Caller", "internal_type": "reference", "mandatory": "false",
         "reference": "sys_user"},
    ]
    result = shape_table_schema(raw)
    assert result[0]["references"] == "sys_user"


def test_schema_strips_sys_id():
    raw = [
        {"element": "sys_id", "column_label": "Sys ID", "internal_type": "GUID", "mandatory": "false", "reference": ""},
        {"element": "short_description", "column_label": "Short description", "internal_type": "string",
         "mandatory": "false", "reference": ""},
    ]
    result = shape_table_schema(raw)
    elements = [f["field"] for f in result]
    assert "sys_id" not in elements
    assert "short_description" in elements
