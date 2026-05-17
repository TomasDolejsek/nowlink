# nowlink/server.py
# FastMCP server definition and tool registration

import httpx
from fastmcp import FastMCP
from nowlink.auth import get_connection_info, get_valid_token, load_credentials
from nowlink.client import query_records, get_record_by_number, get_record_by_sys_id, \
    describe_table as fetch_table_schema
from nowlink.shaper import shape_records, shape_record, shape_table_schema
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
        raw_fields = fetch_table_schema(table)
        shaped = shape_table_schema(raw_fields)
        # Cap at 30 most important fields
        shaped = shaped[:30]
        log_tool_call("describe_table", params, f"{len(shaped)} fields returned for {table}")
        return {
            "table": table,
            "field_count": len(shaped),
            "fields": shaped,
        }
    except Exception as e:
        log_error("describe_table", params, str(e))
        return {"error": str(e), "table": table}
