# nowlink/client.py
# Async HTTP client for the ServiceNow Table API

import httpx
from nowlink.auth import get_valid_token, load_credentials

import os
from dotenv import load_dotenv

load_dotenv()

DEFAULT_PAGE_SIZE = int(os.getenv("NOWLINK_PAGE_SIZE", "20"))
MAX_PAGE_SIZE = 50

# Configurable via NOWLINK_REQUEST_TIMEOUT in .env — default 60s for PDI compatibility.
# Production instances are faster; lower this if needed.
REQUEST_TIMEOUT = int(os.getenv("NOWLINK_REQUEST_TIMEOUT", "60"))


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
    limit = min(limit, MAX_PAGE_SIZE)
    params: dict = {
        "sysparm_limit": str(limit),
        "sysparm_display_value": "all",
        "sysparm_exclude_reference_link": "true",
        "sysparm_order_by_desc": "sys_updated_on",
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
            timeout=REQUEST_TIMEOUT,
        )
    data = _handle_response(response, f"query on {table}")
    return data.get("result", [])


def get_record_by_number(table: str, number: str) -> dict:
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
            timeout=REQUEST_TIMEOUT,
        )
    data = _handle_response(response, f"get {number} from {table}")
    results = data.get("result", [])
    if not results:
        raise RuntimeError(f"Record {number} not found in table {table}.")
    return results[0]


def get_record_by_sys_id(table: str, sys_id: str) -> dict:
    with httpx.Client(verify=False) as client:
        response = client.get(
            f"{_get_base_url()}/api/now/table/{table}/{sys_id}",
            headers=_auth_headers(),
            params={
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
            },
            timeout=REQUEST_TIMEOUT,
        )
    data = _handle_response(response, f"get sys_id={sys_id} from {table}")
    return data.get("result", {})


def create_record(table: str, fields: dict) -> dict:
    """
    Create a new record in the given table.
    fields: dict of {field_name: raw_value} — raw ServiceNow field codes, not display values.
    Returns the created record (raw, with sys_id and number).

    If the POST times out (common on idle PDIs), automatically verifies whether the
    record was actually created by querying the table for a matching record within
    the last 2 minutes. Returns the found record on success, re-raises on genuine failure.
    """
    try:
        with httpx.Client(verify=False) as client:
            response = client.post(
                f"{_get_base_url()}/api/now/table/{table}",
                headers=_auth_headers(),
                json=fields,
                params={
                    "sysparm_display_value": "all",
                    "sysparm_exclude_reference_link": "true",
                },
                timeout=REQUEST_TIMEOUT,
            )
        data = _handle_response(response, f"create on {table}")
        return data.get("result", {})

    except httpx.TimeoutException:
        # PDI timed out — the write may have completed on ServiceNow's side.
        # Verify by querying for a recently created record matching the key fields.
        return _verify_create(table, fields)


def _verify_create(table: str, fields: dict) -> dict:
    """
    Called after a POST timeout on create_record.
    Queries the table for a record matching submitted fields, created in the last
    2 minutes. Returns the raw record if found, raises a clear error if not.

    Uses short_description as the primary match key — present on every ITSM table.
    Falls back to the first field in the submitted dict if short_description is absent.
    """
    from datetime import datetime, timedelta, timezone

    # Build a time-bounded query — records created in the last 2 minutes
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=2)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

    # Pick the best field to match on
    match_field = None
    match_value = None
    if "short_description" in fields:
        match_field = "short_description"
        match_value = fields["short_description"]
    else:
        # Use the first non-system field in the submitted dict as fallback
        for k, v in fields.items():
            if not k.startswith("sys_"):
                match_field = k
                match_value = v
                break

    if not match_field:
        raise RuntimeError(
            "Create timed out and no suitable field found to verify whether "
            "the record was created. Check ServiceNow manually."
        )

    query = f"{match_field}={match_value}^sys_created_on>={cutoff_str}"

    params = {
        "sysparm_query": query,
        "sysparm_limit": "1",
        "sysparm_display_value": "all",
        "sysparm_exclude_reference_link": "true",
        "sysparm_order_by_desc": "sys_created_on",
    }

    with httpx.Client(verify=False) as client:
        response = client.get(
            f"{_get_base_url()}/api/now/table/{table}",
            headers=_auth_headers(),
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

    data = _handle_response(response, f"post-timeout verification on {table}")
    results = data.get("result", [])

    if results:
        # Record found — the POST completed despite the timeout
        return results[0]

    raise RuntimeError(
        "Create timed out and no matching record was found in ServiceNow. "
        "The record was likely not created. Please try again."
    )


def update_record(table: str, sys_id: str, fields: dict) -> dict:
    """
    Update an existing record by sys_id using PATCH.
    fields: dict of {field_name: raw_value} — only the fields to change.
    Returns the updated record (raw).

    Always uses sys_id, never record number. The caller is responsible for
    resolving the number to a sys_id before calling this function.
    PATCH is used (not PUT) so only specified fields are changed — PUT would
    overwrite unspecified fields with empty values.

    If the PATCH times out (common on idle PDIs), automatically verifies whether
    the update was applied by re-fetching the record and checking the changed fields.
    """
    try:
        with httpx.Client(verify=False) as client:
            response = client.patch(
                f"{_get_base_url()}/api/now/table/{table}/{sys_id}",
                headers=_auth_headers(),
                json=fields,
                params={
                    "sysparm_display_value": "all",
                    "sysparm_exclude_reference_link": "true",
                },
                timeout=REQUEST_TIMEOUT,
            )
        data = _handle_response(response, f"update sys_id={sys_id} on {table}")
        return data.get("result", {})

    except httpx.TimeoutException:
        # PDI timed out — verify whether the update actually applied.
        return _verify_update(table, sys_id, fields)


def _verify_update(table: str, sys_id: str, fields: dict) -> dict:
    """
    Called after a PATCH timeout on update_record.
    Re-fetches the record by sys_id and checks whether the submitted field values
    are now present. If yes — returns the current record (update succeeded).
    If no — raises a clear error confirming the update did not apply.
    """
    with httpx.Client(verify=False) as client:
        response = client.get(
            f"{_get_base_url()}/api/now/table/{table}/{sys_id}",
            headers=_auth_headers(),
            params={
                "sysparm_display_value": "all",
                "sysparm_exclude_reference_link": "true",
            },
            timeout=REQUEST_TIMEOUT,
        )

    data = _handle_response(response, f"post-timeout verification on {table}/{sys_id}")
    result = data.get("result", {})

    if not result:
        raise RuntimeError(
            "Update timed out and the record could not be retrieved for verification. "
            "Check ServiceNow manually."
        )

    # Check each submitted field against what's now in ServiceNow.
    # With sysparm_display_value=all, values are dicts — compare against raw "value" key.
    mismatches = []
    for field, submitted_value in fields.items():
        current = result.get(field)
        if isinstance(current, dict):
            current_raw = current.get("value", "")
        else:
            current_raw = str(current) if current is not None else ""
        if str(submitted_value).strip() != str(current_raw).strip():
            mismatches.append(field)

    if mismatches:
        raise RuntimeError(
            f"Update timed out and verification shows {len(mismatches)} field(s) "
            f"did not apply: {', '.join(mismatches)}. Check ServiceNow manually."
        )

    # All submitted fields match — update succeeded despite timeout.
    return result


def bulk_query(table: str, sysparm_query: str) -> tuple[int, list[dict]]:
    """
    Count records matching a query and return the first 5 shaped for preview.

    Used exclusively by bulk_preview to:
    1. Check whether the operation would exceed the 500-record hard limit
    2. Show the user a sample of affected records before they confirm

    Returns: (total_count, first_5_raw_records)

    Uses sysparm_query with sysparm_limit=1 + X-Total-Count header for the
    count — avoids fetching all records just to count them. Then fetches 5
    for the sample.

    Note: ServiceNow's X-Total-Count header reflects the true count for the
    query, not just the page size. Reliable for limit enforcement.
    """
    # Step 1: get the true total count via a limit-1 request + response header
    count_params = {
        "sysparm_limit": "1",
        "sysparm_query": sysparm_query,
        "sysparm_display_value": "true",
        "sysparm_exclude_reference_link": "true",
    }
    try:
        with httpx.Client(verify=False) as client:
            count_response = client.get(
                f"{_get_base_url()}/api/now/table/{table}",
                headers=_auth_headers(),
                params=count_params,
                timeout=REQUEST_TIMEOUT,
            )
        _handle_response(count_response, f"bulk count on {table}")

        # X-Total-Count is set by ServiceNow when sysparm_no_count is not true
        total_count = int(count_response.headers.get("X-Total-Count", "0"))
    except Exception as e:
        raise RuntimeError(f"Could not count records in {table}: {e}")

    # Step 2: fetch first 5 for the sample preview
    sample_params = {
        "sysparm_limit": "5",
        "sysparm_query": sysparm_query,
        "sysparm_display_value": "all",
        "sysparm_exclude_reference_link": "true",
        "sysparm_order_by_desc": "sys_updated_on",
    }
    try:
        with httpx.Client(verify=False) as client:
            sample_response = client.get(
                f"{_get_base_url()}/api/now/table/{table}",
                headers=_auth_headers(),
                params=sample_params,
                timeout=REQUEST_TIMEOUT,
            )
        sample_data = _handle_response(sample_response, f"bulk sample on {table}")
        sample_records = sample_data.get("result", [])
    except Exception:
        sample_records = []

    return total_count, sample_records


def describe_table(table: str) -> list[dict]:
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
            timeout=REQUEST_TIMEOUT,
        )
    data = _handle_response(response, f"describe {table}")
    return data.get("result", [])
