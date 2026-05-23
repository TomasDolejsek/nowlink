# nowlink/safety.py
# Pre-write validation, diff generation, and write audit logging.
#
# Three responsibilities:
#   1. validate_fields()  — check mandatory fields before a create
#   2. diff_fields()      — produce a human-readable before→after diff for updates
#   3. log_write()        — append a structured entry to the write audit log
#
# Used by server.py for create_record and update_record tools.
# Will be reused by v0.3 bulk operations without modification.

import json
from datetime import datetime
from pathlib import Path

WRITE_LOG_DIR = Path.home() / ".nowlink" / "logs"


# ── 1. Field validation ───────────────────────────────────────────────────────

def validate_fields(proposed: dict, table_schema: list[dict]) -> list[str]:
    """
    Check that all mandatory fields in the table schema are present and non-empty
    in the proposed field dict.

    Parameters:
    - proposed:      dict of {field_name: value} that the caller wants to write
    - table_schema:  list of field dicts as returned by shaper.shape_table_schema()
                     Each entry has at minimum: {"field": str, "label": str, "type": str}
                     Mandatory fields also have {"mandatory": True}

    Returns a list of error strings — empty list means the proposed fields are valid.

    Example errors:
      ["short_description is mandatory but was not provided",
       "caller_id is mandatory but was not provided"]
    """
    errors = []
    for field_meta in table_schema:
        if not field_meta.get("mandatory"):
            continue
        field_name = field_meta["field"]
        label = field_meta.get("label", field_name)
        value = proposed.get(field_name)
        if value is None or str(value).strip() == "":
            errors.append(f"{label} ({field_name}) is mandatory but was not provided")
    return errors


# ── 2. Diff generation ────────────────────────────────────────────────────────

def diff_fields(current: dict, proposed: dict) -> dict:
    """
    Produce a structured diff between the current shaped record and the proposed changes.

    Parameters:
    - current:   shaped record dict (as returned by shaper.shape_record) —
                 human-readable display values, not raw codes
    - proposed:  dict of {field_name: new_value} that the caller wants to write —
                 these are the RAW values Claude will send to ServiceNow
                 (e.g. "1" for priority, not "P1 - Critical")

    Returns a dict with three keys:
    - "changes":    list of {field, from, to} for fields that will change
    - "unchanged":  list of field names where proposed value matches current display value
    - "new_fields": list of field names being set that don't exist on the current record

    The "changes" list is what gets shown to the user during the preview step.

    Note on value comparison:
    current values are display values (shaped), proposed values are raw codes.
    We show the current display value as "from" and the proposed raw value as "to".
    This is intentional — the user sees where they're coming FROM (readable) and
    what they're asking to set TO (what they typed). The actual write uses the raw value.
    """
    changes = []
    unchanged = []
    new_fields = []

    for field, new_value in proposed.items():
        new_str = str(new_value).strip()
        if field in current:
            current_str = str(current[field]).strip()
            if current_str == new_str:
                unchanged.append(field)
            else:
                changes.append({
                    "field": field,
                    "from": current[field],
                    "to": new_value,
                })
        else:
            new_fields.append({"field": field, "to": new_value})

    return {
        "changes": changes,
        "unchanged": unchanged,
        "new_fields": new_fields,
    }


# ── 3. Write audit logging ────────────────────────────────────────────────────

def log_write(
    operation: str,
    table: str,
    identifier: str,
    fields_written: dict,
    diff: dict | None = None,
):
    """
    Append a structured write entry to the daily write audit log.
    File: ~/.nowlink/logs/writes-YYYY-MM-DD.log

    Parameters:
    - operation:     "create" or "update"
    - table:         ServiceNow table name (e.g. "incident")
    - identifier:    Record number or sys_id that was written (e.g. "INC0012345")
    - fields_written: The raw field dict that was sent to ServiceNow
    - diff:          Output of diff_fields() for updates — None for creates

    Log format (one JSON object per line, human-readable with indent=None):
    {"ts": "2026-05-17T14:23:01", "op": "update", "table": "incident",
     "record": "INC0000055", "fields": {"priority": "1"}, "changes": [...]}
    """
    WRITE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = WRITE_LOG_DIR / f"writes-{today}.log"

    entry = {
        "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "op": operation,
        "table": table,
        "record": identifier,
        "fields": fields_written,
    }
    if diff is not None:
        entry["changes"] = diff.get("changes", [])

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
