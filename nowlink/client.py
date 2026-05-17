# nowlink/client.py
# Async HTTP client for the ServiceNow Table API

import httpx
from nowlink.auth import get_valid_token, load_credentials

import os
from dotenv import load_dotenv

load_dotenv()

DEFAULT_PAGE_SIZE = int(os.getenv("NOWLINK_PAGE_SIZE", "20"))
MAX_PAGE_SIZE = 50


def _get_base_url() -> str:
    creds = load_credentials()
    return creds["instance_url"]


def _auth_headers() -> dict:
    token = get_valid_token()
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _handle_response(response: httpx.Response, context: str) -> dict:
    """Raise a clear error for bad HTTP responses."""
    if response.status_code == 401:
        raise RuntimeError("Authentication failed — run `nowlink init` to re-configure credentials.")
    if response.status_code == 403:
        raise RuntimeError(
            f"Access denied on {context}. "
            "Check that nowlink.dev has the required roles for this table."
        )
    if response.status_code == 404:
        raise RuntimeError(f"Not found: {context}")
    if response.status_code == 429:
        raise RuntimeError("ServiceNow rate limit hit. Wait a moment and try again.")
    if response.status_code >= 400:
        # Try to extract ServiceNow's error message
        try:
            body = response.json()
            detail = body.get("error", {}).get("message") or body.get("error", {}).get("detail") or response.text
        except Exception:
            detail = response.text
        raise RuntimeError(f"ServiceNow error on {context}: {detail}")
    return response.json()


# ── Table API calls ───────────────────────────────────────────────────────────

def query_records(table: str, sysparm_query: str = "", fields: list[str] | None = None,
                  limit: int = DEFAULT_PAGE_SIZE) -> list[dict]:
    """
    Query a ServiceNow table and return raw records.
    limit is capped at MAX_PAGE_SIZE to protect token budgets.
    """
    limit = min(limit, MAX_PAGE_SIZE)

    params: dict = {
        "sysparm_limit": str(limit),
        "sysparm_display_value": "all",  # returns both display value and raw value
        "sysparm_exclude_reference_link": "true",  # suppress link objects for references
        "sysparm_order_by_desc": "sys_updated_on",  # most recently updated first
    }
    if sysparm_query:
        params["sysparm_query"] = sysparm_query
    if fields:
        params["sysparm_fields"] = ",".join(fields)

    with httpx.Client(verify=False) as client:
        response = client.get(
            f"{_get_base_url()}/api/now/table/{table}",
            headers=_auth_headers(),
            params=params,
            timeout=30,
        )

    data = _handle_response(response, f"query on {table}")
    return data.get("result", [])


def get_record_by_number(table: str, number: str) -> dict:
    """
    Fetch a single record by its display number (e.g. INC0001234).
    Returns the raw record dict, or raises if not found.
    """
    params = {
        "sysparm_query": f"number={number}",
        "sysparm_limit": "1",
        "sysparm_display_value": "all",
        "sysparm_exclude_reference_link": "true",
    }

    with httpx.Client(verify=False) as client:
        response = client.get(
            f"{_get_base_url()}/api/now/table/{table}",
            headers=_auth_headers(),
            params=params,
            timeout=30,
        )

    data = _handle_response(response, f"get {number} from {table}")
    results = data.get("result", [])
    if not results:
        raise RuntimeError(f"Record {number} not found in table {table}.")
    return results[0]


def get_record_by_sys_id(table: str, sys_id: str) -> dict:
    """Fetch a single record by sys_id."""
    with httpx.Client(verify=False) as client:
        response = client.get(
            f"{_get_base_url()}/api/now/table/{table}/{sys_id}",
            headers=_auth_headers(),
            params={
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
            },
            timeout=30,
        )

    data = _handle_response(response, f"get sys_id={sys_id} from {table}")
    return data.get("result", {})


def describe_table(table: str) -> list[dict]:
    """
    Return field metadata for a table via sys_dictionary.
    Returns up to 80 fields (filtered to meaningful ones in shaper.py).
    """
    params = {
        "sysparm_query": f"name={table}^element!=NULL^active=true",
        "sysparm_fields": "element,column_label,internal_type,mandatory,max_length,reference",
        "sysparm_limit": "80",
        "sysparm_display_value": "true",
    }

    with httpx.Client(verify=False) as client:
        response = client.get(
            f"{_get_base_url()}/api/now/table/sys_dictionary",
            headers=_auth_headers(),
            params=params,
            timeout=30,
        )

    data = _handle_response(response, f"describe {table}")
    return data.get("result", [])
