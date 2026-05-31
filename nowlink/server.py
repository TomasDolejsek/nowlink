# nowlink/server.py
# FastMCP server definition and tool registration

import httpx
from fastmcp import FastMCP
from nowlink.auth import get_connection_info, get_valid_token, load_credentials
from nowlink.client import query_records, get_record_by_number, get_record_by_sys_id, \
    describe_table as fetch_table_schema, create_record as client_create, \
    update_record as client_update
from nowlink.shaper import shape_records, shape_record, shape_table_schema, TABLE_FIELDS
from nowlink.safety import diff_fields, log_write
from nowlink.logger import log_tool_call, log_error

mcp = FastMCP("nowlink")


# ── ping ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def ping() -> dict:
    """
    Check if NowLink is connected to ServiceNow and return the instance details.
    Use this to verify the connection is working before running other tools.
    """
    info = get_connection_info()
    token = get_valid_token()
    creds = load_credentials()

    response = httpx.get(
        f"{creds['instance_url']}/api/now/table/sys_user",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "sysparm_query": f"user_name={creds['username']}",
            "sysparm_fields": "user_name,name",
            "sysparm_limit": "1",
        },
        verify=False,
    )

    return {
        "status": "connected",
        "instance": info["instance_url"],
        "user": info["username"],
        "message": f"NowLink is connected to {info['instance_url']} as {info['username']}",
    }


# ── query ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def query(
        table: str,
        filters: str = "",
        limit: int = 20,
) -> dict:
    """
    Query any ServiceNow table and return clean, readable records.

    Use this tool when:
    - The user asks to find, list, search, or show records from any table
    - The user asks questions like "show me all P1 incidents" or "find open problems"
    - You need multiple records (not a single known record)

    Do NOT use this tool to fetch a single known record by number — use get_record instead.

    Parameters:
    - table: ServiceNow table name (e.g. "incident", "problem", "change_request", "task")
    - filters: An encoded ServiceNow query string (e.g. "priority=1^state=1^active=true").
      Leave empty to return the most recent records.
      Common field names: state, priority, assignment_group, assigned_to, caller_id,
      opened_at, resolved_at, short_description (use LIKE for text search).
      Common state values: incident states 1=New 2=InProgress 6=Resolved 7=Closed.
      Common priority values: 1=Critical 2=High 3=Moderate 4=Low.
    - limit: Number of records to return. Default 20. Maximum 50.

    Returns a list of shaped records with human-readable field values.
    Reference fields (like assigned_to) are resolved to display names, not sys_ids.
    Priority and state codes are translated to labels.
    """
    params = {"table": table, "filters": filters, "limit": limit}
    try:
        raw = query_records(table, sysparm_query=filters, limit=limit)
        shaped = shape_records(raw, table)
        log_tool_call("query", params, f"{len(shaped)} records returned")
        return {
            "table": table,
            "count": len(shaped),
            "records": shaped,
        }
    except Exception as e:
        log_error("query", params, str(e))
        return {"error": str(e), "table": table}


# ── get_record ────────────────────────────────────────────────────────────────

@mcp.tool()
def get_record(
        table: str,
        identifier: str,
) -> dict:
    """
    Fetch a single ServiceNow record by its number or sys_id.

    Use this tool when:
    - The user asks about a specific record and provides a number (e.g. INC0001234, PRB0000012)
    - You already know the sys_id of the record you need
    - You need full detail on one specific record

    Do NOT use this tool to search for records — use query instead.

    Parameters:
    - table: ServiceNow table name (e.g. "incident", "problem", "change_request")
    - identifier: The record number (e.g. "INC0001234") or sys_id (32-character hex string).
      Record numbers start with a prefix: INC=incident, PRB=problem, CHG=change_request,
      REQ=sc_request, RITM=sc_req_item, TASK=task.

    Returns a single shaped record with all relevant fields resolved to human-readable values.
    """
    params = {"table": table, "identifier": identifier}
    try:
        # sys_id is a 32-char hex string; numbers have letter prefixes
        if len(identifier) == 32 and all(c in "0123456789abcdef" for c in identifier.lower()):
            raw = get_record_by_sys_id(table, identifier)
        else:
            raw = get_record_by_number(table, identifier)

        shaped = shape_record(raw, table)
        log_tool_call("get_record", params, f"returned {identifier}")
        return shaped
    except Exception as e:
        log_error("get_record", params, str(e))
        return {"error": str(e)}


# ── describe_table ────────────────────────────────────────────────────────────

@mcp.tool()
def describe_table(table: str) -> dict:
    """
    Return the field schema for a ServiceNow table.

    Use this tool when:
    - The user asks what fields a table has
    - You need to know field names before constructing a query filter
    - You need to understand what values are valid for a field
    - A previous query returned unexpected fields and you need to check the schema

    Parameters:
    - table: ServiceNow table name (e.g. "incident", "problem", "change_request", "cmdb_ci")

    Returns up to 30 fields with their API field name, display label, data type,
    whether they are mandatory, and the table they reference (for reference fields).
    Mandatory fields are listed first.
    """
    params = {"table": table}
    try:
        # For tables with a known allowlist, build the schema from that instead of
        # querying sys_dictionary. sys_dictionary misses inherited fields — e.g.
        # short_description on incident is inherited from task and won't appear
        # under name=incident. The allowlist is what NowLink actually returns,
        # so it's the accurate answer to "what fields does this table have?"
        if table in TABLE_FIELDS:
            mandatory = set()
            shaped = [
                {
                    "field": field,
                    "label": field.replace("_", " ").title(),
                    "type": "string",
                    **({"mandatory": True} if field in mandatory else {}),
                }
                for field in TABLE_FIELDS[table]
            ]
            # Mandatory fields first, then preserve allowlist order
            shaped.sort(key=lambda x: (not x.get("mandatory", False), TABLE_FIELDS[table].index(x["field"])))
            source = "allowlist"
        else:
            # Unknown table — fall back to sys_dictionary
            raw_fields = fetch_table_schema(table)
            shaped = shape_table_schema(raw_fields)
            shaped = shaped[:30]
            source = "sys_dictionary"

        log_tool_call("describe_table", params, f"{len(shaped)} fields returned for {table} (source: {source})")
        return {
            "table": table,
            "field_count": len(shaped),
            "fields": shaped,
            "source": source,
        }
    except Exception as e:
        log_error("describe_table", params, str(e))
        return {"error": str(e), "table": table}


# ── create_record ─────────────────────────────────────────────────────────────

@mcp.tool()
def create_record(
        table: str,
        fields: dict,
        confirm: bool = False,
) -> dict:
    """
    Create a new record in a ServiceNow table.

    IMPORTANT — TWO-STEP PATTERN. Always call this tool TWICE:
      Step 1: confirm=False (default) — shows a preview of what WOULD be created.
              Present this preview to the user and ask "Shall I go ahead?"
      Step 2: confirm=True — only after the user explicitly says yes.

    Never skip the preview step. Never call with confirm=True on the first attempt.

    Parameters:
    - table:   ServiceNow table name (e.g. "incident", "problem", "change_request")
    - fields:  Dict of field names and values to set on the new record.
               CRITICAL — field values must be RAW ServiceNow codes, not display labels:
                 priority:  "1"=Critical  "2"=High  "3"=Moderate  "4"=Low
                 impact:    "1"=High  "2"=Medium  "3"=Low
                 urgency:   "1"=High  "2"=Medium  "3"=Low
                 state:     incident: "1"=New  "2"=In Progress  "6"=Resolved
               Reference fields (assigned_to, caller_id, assignment_group) accept
               either sys_id or user_name/name — ServiceNow resolves them.
               Text fields (short_description, description) accept plain strings.
- confirm: False = preview only (default). True = execute the create.

    Mandatory fields for incident: short_description, caller_id
    Mandatory fields for problem: short_description
    Mandatory fields for change_request: short_description, category

    Returns on preview (confirm=False):
      {"preview": true, "table": ..., "fields_to_create": {...}, "validation_errors": [...]}
    Returns on success (confirm=True):
      {"created": true, "table": ..., "number": "INC0012345", "sys_id": "...", "record": {...}}
    """
    params = {"table": table, "fields": fields, "confirm": confirm}

    try:
        # Inject caller_id default for incident if not provided.
        if table == "incident" and "caller_id" not in fields:
            creds = load_credentials()
            fields = {**fields, "caller_id": creds["username"]}

        if not confirm:
            log_tool_call("create_record:preview", params, f"preview for {table}")
            return {
                "preview": True,
                "table": table,
                "fields_to_create": fields,
                "message": (
                    f"Ready to create a new {table} record with the above fields. "
                    "Call again with confirm=True to proceed."
                ),
            }

        # confirm=True — execute the write
        raw_result = client_create(table, fields)
        shaped_result = shape_record(raw_result, table)
        number = shaped_result.get("number") or raw_result.get("number", {}).get("value", "unknown")
        sys_id = raw_result.get("sys_id", {}).get("value", "") if isinstance(raw_result.get("sys_id"), dict) else raw_result.get("sys_id", "")

        log_write("create", table, number, fields)
        log_tool_call("create_record", params, f"created {number}")

        return {
            "created": True,
            "table": table,
            "number": number,
            "sys_id": sys_id,
            "record": shaped_result,
        }

    except Exception as e:
        log_error("create_record", params, str(e))
        return {"error": str(e), "table": table}


# ── update_record ─────────────────────────────────────────────────────────────

@mcp.tool()
def update_record(
        table: str,
        identifier: str,
        fields: dict,
        confirm: bool = False,
) -> dict:
    """
    Update a single existing ServiceNow record.

    IMPORTANT — TWO-STEP PATTERN. Always call this tool TWICE:
      Step 1: confirm=False (default) — fetches the current record and shows exactly
              which fields will change (before → after). Present this diff to the user
              and ask "Shall I go ahead?"
      Step 2: confirm=True — only after the user explicitly says yes.

    Never skip the preview step. Never call with confirm=True on the first attempt.
    This tool updates ONE record only. Never call it in a loop to update multiple records.
    For bulk updates use bulk_preview + bulk_execute (v0.3).

    Parameters:
    - table:      ServiceNow table name (e.g. "incident", "problem", "change_request")
    - identifier: Record number (e.g. "INC0001234") or 32-character sys_id hex string.
    - fields:     Dict of field names and values to change. Only include fields you want
                  to modify — unspecified fields are not touched (PATCH semantics).
                  CRITICAL — field values must be RAW ServiceNow codes, not display labels:
                    priority:  "1"=Critical  "2"=High  "3"=Moderate  "4"=Low
                    impact:    "1"=High  "2"=Medium  "3"=Low
                    urgency:   "1"=High  "2"=Medium  "3"=Low
                    state:     incident: "1"=New "2"=In Progress "6"=Resolved "7"=Closed
                               problem:  "101"=New "106"=Resolved "107"=Closed
                               change:   "-5"=New "-1"=Implement "3"=Closed
                  Reference fields (assigned_to, caller_id, assignment_group) accept
                  either sys_id or user_name/name — ServiceNow resolves them.
                  Text fields accept plain strings.
    - confirm:    False = preview only (default). True = execute the update.

    Returns on preview (confirm=False):
      {"preview": true, "record": "INC0001234", "changes": [{"field":..,"from":..,"to":..}],
       "unchanged": [...], "new_fields": [...]}
    Returns on success (confirm=True):
      {"updated": true, "record": "INC0001234", "changes": [...], "result": {shaped record}}
    """
    params = {"table": table, "identifier": identifier, "fields": fields, "confirm": confirm}

    try:
        # Resolve identifier to a raw record (need both sys_id and current shaped state)
        is_sys_id = len(identifier) == 32 and all(c in "0123456789abcdef" for c in identifier.lower())
        if is_sys_id:
            raw_current = get_record_by_sys_id(table, identifier)
        else:
            raw_current = get_record_by_number(table, identifier)

        # Shape current record for diff display and for extracting sys_id
        shaped_current = shape_record(raw_current, table)
        number = shaped_current.get("number", identifier)

        # Extract sys_id from raw response for the actual PATCH call.
        # With sysparm_display_value=all, sys_id is {"display_value": "...", "value": "..."}
        sys_id_field = raw_current.get("sys_id")
        if isinstance(sys_id_field, dict):
            sys_id = sys_id_field.get("value", "")
        else:
            sys_id = sys_id_field or ""

        if not sys_id:
            raise RuntimeError(
                f"Could not extract sys_id from {identifier}. "
                "Cannot perform update without a valid sys_id."
            )

        # Generate diff — what will change, what won't
        diff = diff_fields(shaped_current, fields)

        if not confirm:
            # Preview mode — return diff, write nothing
            log_tool_call("update_record:preview", params, f"preview for {number}")
            message = (
                f"No fields will change on {number}."
                if not diff["changes"] and not diff["new_fields"]
                else f"Ready to update {number}. Call again with confirm=True to proceed."
            )
            return {
                "preview": True,
                "table": table,
                "record": number,
                "changes": diff["changes"],
                "unchanged": diff["unchanged"],
                "new_fields": diff["new_fields"],
                "message": message,
            }

        # confirm=True — execute the write
        if not fields:
            return {"error": "No fields provided to update.", "record": number}

        raw_result = client_update(table, sys_id, fields)
        shaped_result = shape_record(raw_result, table)

        log_write("update", table, number, fields, diff)
        log_tool_call("update_record", params, f"updated {number} (sys_id={sys_id}), {len(diff['changes'])} field(s) changed")

        return {
            "updated": True,
            "table": table,
            "record": number,
            "changes": diff["changes"],
            "result": shaped_result,
        }

    except Exception as e:
        log_error("update_record", params, str(e))
        return {"error": str(e)}
