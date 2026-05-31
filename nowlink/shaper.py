# nowlink/shaper.py
# Transforms raw ServiceNow API responses into clean, token-efficient output.
#
# ServiceNow returns 40-80 fields per record by default.
# An LLM only needs 6-12 meaningful fields to reason correctly.
# Returning everything wastes tokens and causes hallucination of field names.
#
# This module handles three things:
#   1. Field allowlists — per-table lists of fields that actually matter
#   2. Value mapping — turning "1" into "P1", state codes into human labels
#   3. Reference flattening — turning {"display_value": "John Smith", "value": "abc123"}
#      into just "John Smith"

# ── System fields to always strip ────────────────────────────────────────────
# These exist on every ServiceNow record and are never useful to an LLM.

STRIP_ALWAYS = {
    "sys_mod_count",
    "sys_class_name",
    "sys_tags",
    "sys_updated_by",
    "sys_created_by",
    "upon_approval",
    "upon_reject",
    "delivery_plan",
    "delivery_task",
    "calendar_duration",
    "time_worked",
    "correlation_display",
    "correlation_id",
    "location",  # raw sys_id — meaningless without resolution
}

# ── Value maps ────────────────────────────────────────────────────────────────

PRIORITY_MAP = {
    "1": "P1 - Critical",
    "2": "P2 - High",
    "3": "P3 - Moderate",
    "4": "P4 - Low",
    "5": "P5 - Planning",
}

IMPACT_MAP = {
    "1": "High",
    "2": "Medium",
    "3": "Low",
}

URGENCY_MAP = {
    "1": "High",
    "2": "Medium",
    "3": "Low",
}

INCIDENT_STATE_MAP = {
    "1": "New",
    "2": "In Progress",
    "3": "On Hold",
    "6": "Resolved",
    "7": "Closed",
    "8": "Cancelled",
}

PROBLEM_STATE_MAP = {
    "101": "New",
    "102": "Assess",
    "103": "Root Cause Analysis",
    "104": "Fix in Progress",
    "106": "Resolved",
    "107": "Closed",
    "108": "Cancelled",
}

CHANGE_STATE_MAP = {
    "-5": "New",
    "-4": "Assess",
    "-3": "Authorize",
    "-2": "Scheduled",
    "-1": "Implement",
    "0": "Review",
    "3": "Closed",
    "4": "Cancelled",
}

TASK_STATE_MAP = {
    "-5": "Pending",
    "1": "Open",
    "2": "Work in Progress",
    "3": "Closed Complete",
    "4": "Closed Incomplete",
    "7": "Closed Skipped",
}

APPROVAL_MAP = {
    "not requested": "Not Requested",
    "requested": "Requested",
    "approved": "Approved",
    "rejected": "Rejected",
    "cancelled": "Cancelled",
}

# ── Per-table field allowlists ────────────────────────────────────────────────
# These define which fields are returned when no explicit field list is given.
# Order is preserved in output.

TABLE_FIELDS: dict[str, list[str]] = {
    "incident": [
        "number",
        "short_description",
        "state",
        "priority",
        "impact",
        "urgency",
        "category",
        "subcategory",
        "caller_id",
        "assigned_to",
        "assignment_group",
        "opened_at",
        "resolved_at",
        "close_code",
        "close_notes",
        "description",
        "sys_id",
    ],
    "problem": [
        "number",
        "short_description",
        "state",
        "priority",
        "impact",
        "urgency",
        "assigned_to",
        "assignment_group",
        "opened_at",
        "resolved_at",
        "cause_notes",
        "fix_notes",
        "description",
        "sys_id",
    ],
    "change_request": [
        "number",
        "short_description",
        "state",
        "priority",
        "impact",
        "urgency",
        "type",
        "phase",
        "assigned_to",
        "assignment_group",
        "start_date",
        "end_date",
        "approval",
        "description",
        "sys_id",
    ],
    "sc_request": [
        "number",
        "short_description",
        "state",
        "priority",
        "requested_for",
        "opened_at",
        "description",
        "sys_id",
    ],
    "sc_req_item": [
        "number",
        "short_description",
        "state",
        "price",
        "quantity",
        "request",
        "cat_item",
        "assigned_to",
        "assignment_group",
        "sys_id",
    ],
    "task": [
        "number",
        "short_description",
        "state",
        "priority",
        "assigned_to",
        "assignment_group",
        "opened_at",
        "due_date",
        "description",
        "sys_id",
    ],
    "sys_user": [
        "user_name",
        "name",
        "email",
        "department",
        "title",
        "manager",
        "active",
        "sys_id",
    ],
    "cmdb_ci": [
        "name",
        "short_description",
        "operational_status",
        "install_status",
        "assigned_to",
        "assignment_group",
        "managed_by",
        "sys_class_name",
        "sys_id",
    ],
}


# ── Per-table mandatory fields ────────────────────────────────────────────────
# REMOVED in v0.3.
#
# Original approach: hardcoded map of known-mandatory fields per table.
# Problem: sys_dictionary.mandatory reflects database-level constraints only.
# Most "mandatory" fields in the ServiceNow UI are enforced by UI Policies,
# which only fire in the browser — the REST API doesn't see them at all.
# On a standard PDI, short_description on incident has mandatory=false in
# sys_dictionary, even though it's required in the UI.
#
# Decision: remove pre-flight mandatory validation entirely. Let ServiceNow
# reject invalid creates and surface the error clearly through Claude.
# ServiceNow is the authority. NowLink communicates the rejection cleanly.
# See docs/decisions/v0.3-known-issues.md for the full rationale.


# ── Core shaping logic ────────────────────────────────────────────────────────

def _resolve_field_value(field_name: str, raw_value) -> str | None:
    """
    ServiceNow returns reference fields and choice fields as dicts when
    sysparm_display_value=all is used:
      {"display_value": "John Smith", "value": "abc123sys_id"}

    For display_value=all, each field is a dict with "display_value" and "value".
    We prefer display_value for human-readable output, but apply our own value
    maps for coded fields like priority and state.
    """
    if raw_value is None:
        return None

    # With sysparm_display_value=all, every field is {"display_value": X, "value": Y}
    if isinstance(raw_value, dict):
        display = raw_value.get("display_value")
        raw = raw_value.get("value")

        # Apply our own maps for fields we know
        mapped = _apply_value_map(field_name, raw)
        if mapped is not None:
            return mapped

        # Fall back to ServiceNow's display_value
        if display and display != "":
            return display

        # Last resort: raw value
        return raw if raw else None

    # Scalar value (shouldn't happen with display_value=all but handle it)
    mapped = _apply_value_map(field_name, str(raw_value))
    return mapped if mapped is not None else str(raw_value)


def _apply_value_map(field_name: str, value: str | None) -> str | None:
    """Map coded values to human-readable strings for known fields."""
    if value is None:
        return None

    maps = {
        "priority": PRIORITY_MAP,
        "impact": IMPACT_MAP,
        "urgency": URGENCY_MAP,
        "state": None,  # handled per-table below
        "approval": APPROVAL_MAP,
    }

    if field_name == "state":
        # We can't know the table here, so we try all state maps in order
        # The caller (shape_records) will pass table context when possible
        return None  # handled in shape_record with table context

    if field_name in maps and maps[field_name]:
        return maps[field_name].get(value)

    return None


def _resolve_state(value: str | None, table: str) -> str | None:
    """Apply the correct state map for the given table."""
    if value is None:
        return None
    if table in ("incident",):
        return INCIDENT_STATE_MAP.get(value, value)
    if table in ("problem",):
        return PROBLEM_STATE_MAP.get(value, value)
    if table in ("change_request",):
        return CHANGE_STATE_MAP.get(value, value)
    if table in ("task", "sc_req_item", "sc_request"):
        return TASK_STATE_MAP.get(value, value)
    return value


def shape_record(raw: dict, table: str, allowed_fields: list[str] | None = None) -> dict:
    """
    Shape a single raw ServiceNow record into a clean dict.
    - Strips system noise fields
    - Flattens reference field dicts to display values
    - Applies value maps (priority, state, impact, urgency)
    - Filters to the allowlist for this table
    """
    fields_to_include = allowed_fields or TABLE_FIELDS.get(table) or None

    shaped = {}

    # If we have a specific field order (from allowlist), preserve it
    if fields_to_include:
        for field in fields_to_include:
            if field in STRIP_ALWAYS:
                continue
            if field not in raw:
                continue
            raw_value = raw[field]

            if field == "state":
                # Get the raw coded value for state mapping
                raw_code = raw_value.get("value") if isinstance(raw_value, dict) else raw_value
                resolved = _resolve_state(raw_code, table)
                shaped[field] = resolved
            else:
                resolved = _resolve_field_value(field, raw_value)
                if resolved is not None and resolved != "":
                    shaped[field] = resolved
    else:
        # No allowlist — include everything except STRIP_ALWAYS
        for field, raw_value in raw.items():
            if field in STRIP_ALWAYS:
                continue
            if field == "state":
                raw_code = raw_value.get("value") if isinstance(raw_value, dict) else raw_value
                shaped[field] = _resolve_state(raw_code, table)
            else:
                resolved = _resolve_field_value(field, raw_value)
                if resolved is not None and resolved != "":
                    shaped[field] = resolved

    return shaped


def shape_records(raw_list: list[dict], table: str, allowed_fields: list[str] | None = None) -> list[dict]:
    """Shape a list of raw records."""
    return [shape_record(r, table, allowed_fields) for r in raw_list]


def shape_table_schema(raw_fields: list[dict]) -> list[dict]:
    """
    Shape sys_dictionary field metadata into a clean schema description.
    Returns fields sorted: mandatory first, then alphabetical by label.
    Strips internal plumbing fields that aren't useful to an LLM.
    """
    SKIP_ELEMENTS = {
        "sys_id", "sys_mod_count", "sys_updated_on", "sys_updated_by",
        "sys_created_on", "sys_created_by", "sys_class_name", "sys_tags",
        "sys_domain", "sys_domain_path", "sys_overrides",
    }

    shaped = []
    for f in raw_fields:
        element = f.get("element", "")
        if element in SKIP_ELEMENTS:
            continue

        label = f.get("column_label", element)
        field_type = f.get("internal_type", "")
        mandatory = f.get("mandatory", "false") in ("true", True)
        reference = f.get("reference", "")

        entry: dict = {
            "field": element,
            "label": label,
            "type": field_type,
        }
        if mandatory:
            entry["mandatory"] = True
        if reference:
            entry["references"] = reference

        shaped.append(entry)

    # Mandatory fields first, then alphabetical by label
    shaped.sort(key=lambda x: (not x.get("mandatory", False), x.get("label", "").lower()))
    return shaped
