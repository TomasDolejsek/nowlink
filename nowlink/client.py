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
            "ServiceNow did not respond in time. No suitable field found to verify "
            "whether the record was created. Check ServiceNow manually."
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

    # Nothing found — the write did not complete.
    # On incident, a missing short_description is the most common cause:
    # ServiceNow business rules hang on insert without a subject line,
    # the request times out, and nothing is written.
    hint = (
        " Tip: on incident, a missing short_description often causes this — "
        "ServiceNow business rules can hang on insert without a subject line."
        if table == "incident" and "short_description" not in fields
        else ""
    )
    raise RuntimeError(
        f"ServiceNow did not respond in time and the record was not created.{hint} "
        "Please check that all required fields are provided and try again."
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


def get_table_ancestry(table: str) -> list[str]:
    """
    Walk sys_db_object upward via super_class to build the full inheritance chain.

    Example: incident → task → (no further parent)
    Returns: ["incident", "task"]

    Used by get_mandatory_fields() to query sys_dictionary across the full ancestry
    so inherited mandatory fields (e.g. short_description defined on task, inherited
    by incident) are included.

    Returns just [table] if the table has no parent or sys_db_object is inaccessible.
    """
    ancestry = []
    current = table
    seen = set()

    while current and current not in seen:
        ancestry.append(current)
        seen.add(current)

        params = {
            "sysparm_query": f"name={current}",
            "sysparm_fields": "name,super_class.name",
            "sysparm_limit": "1",
            "sysparm_display_value": "true",
        }
        with httpx.Client(verify=False) as client:
            response = client.get(
                f"{_get_base_url()}/api/now/table/sys_db_object",
                headers=_auth_headers(),
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
        data = _handle_response(response, f"sys_db_object lookup for {current}")
        results = data.get("result", [])
        if not results:
            break
        parent = results[0].get("super_class.name") or ""
        current = parent.strip() if parent else ""

    return ancestry


def get_mandatory_fields(table: str) -> set[str]:
    """
    Return all mandatory field names for a table, including inherited ones.

    Walks the full table inheritance chain via get_table_ancestry(), then queries
    sys_dictionary once with nameIN{ancestry} to get all mandatory fields across
    the chain.

    IMPORTANT — this is NowLink's pre-flight validation, not a prediction of what
    ServiceNow will enforce at the API level. The REST API may accept a record
    missing these fields. NowLink validates before the write to give the user a
    clear, early warning rather than a timeout or silent bad data.

    Known blind spot: Data Policies with "Apply to Web Services" checked enforce
    mandatory fields at the server level and WILL cause the REST API to return 400.
    These live in sys_data_policy_rule, not sys_dictionary, and are not checked here.
    If a write is rejected with a 400 due to a Data Policy, NowLink surfaces the
    error clearly — but this pre-flight check will not have warned the user in advance.

    Returns a set of field name strings. Returns empty set if query fails — callers
    handle gracefully (no validation rather than blocking on error).
    """
    ancestry = get_table_ancestry(table)
    if not ancestry:
        return set()

    ancestry_filter = ",".join(ancestry)
    params = {
        "sysparm_query": f"nameIN{ancestry_filter}^mandatory=true^active=true^elementISNOTEMPTY",
        "sysparm_fields": "element",
        "sysparm_limit": "200",
        "sysparm_display_value": "true",
    }
    try:
        with httpx.Client(verify=False) as client:
            response = client.get(
                f"{_get_base_url()}/api/now/table/sys_dictionary",
                headers=_auth_headers(),
                params=params,
                timeout=REQUEST_TIMEOUT,
            )
        data = _handle_response(response, f"mandatory fields for {table}")
        results = data.get("result", [])
        return {r["element"] for r in results if r.get("element")}
    except Exception:
        return set()


def bulk_update(table: str, sys_ids: list[str], fields: dict) -> None:
    """
    Update multiple records using the ServiceNow Batch API in chunks.
    POST /api/now/v1/batch — one HTTP round trip per chunk of 50 records.

    Sub-request status codes are intentionally ignored — PDI transaction
    timeouts cause false negatives (500 returned but write completed).
    Callers verify actual outcome via a post-execution re-count query.

    Raises RuntimeError only if the outer batch HTTP call itself fails (4xx/5xx
    on the batch endpoint, not on individual sub-requests).
    """
    import json as _json
    import base64
    import time

    BATCH_CHUNK_SIZE = 50
    CHUNK_SLEEP = float(os.getenv("NOWLINK_BULK_CHUNK_SLEEP", "1.0"))

    base_url = _get_base_url()
    fields_b64 = base64.b64encode(_json.dumps(fields).encode()).decode()

    for chunk_start in range(0, len(sys_ids), BATCH_CHUNK_SIZE):
        chunk = sys_ids[chunk_start:chunk_start + BATCH_CHUNK_SIZE]

        requests = [
            {
                "id": str(chunk_start + i),
                "method": "PATCH",
                "url": f"/api/now/table/{table}/{sys_id}",
                "headers": [{"name": "Content-Type", "value": "application/json"}],
                "body": fields_b64,
            }
            for i, sys_id in enumerate(chunk)
        ]

        with httpx.Client(verify=False) as client:
            response = client.post(
                f"{base_url}/api/now/v1/batch",
                headers=_auth_headers(),
                json={
                    "batch_request_id": f"nowlink-bulk-{chunk_start}",
                    "rest_requests": requests,
                },
                timeout=max(REQUEST_TIMEOUT, 120),
            )

        # Only raise if the batch endpoint itself fails
        if response.status_code >= 400:
            raise RuntimeError(
                f"Batch API error {response.status_code} on chunk starting at {chunk_start}: "
                f"{response.text[:200]}"
            )

        # Sub-request statuses intentionally ignored — verified by caller re-count
        if chunk_start + BATCH_CHUNK_SIZE < len(sys_ids):
            time.sleep(CHUNK_SLEEP)


def bulk_fetch_sys_ids(table: str, sysparm_query: str, limit: int = 500) -> list[dict]:
    """
    Fetch sys_ids and numbers for all records matching the query, up to limit.
    Used exclusively by bulk_execute to get the full list of records to update.
    Bypasses the MAX_PAGE_SIZE cap — bulk operations need up to 500 records.
    Returns minimal fields: sys_id and number only.
    """
    params = {
        "sysparm_limit": str(limit),
        "sysparm_query": sysparm_query,
        "sysparm_fields": "sys_id,number",
        "sysparm_display_value": "false",
        "sysparm_exclude_reference_link": "true",
    }
    with httpx.Client(verify=False) as client:
        response = client.get(
            f"{_get_base_url()}/api/now/table/{table}",
            headers=_auth_headers(),
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
    data = _handle_response(response, f"bulk fetch sys_ids on {table}")
    return data.get("result", [])


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


# ── Flow Bridge ───────────────────────────────────────────────────────────────

# The Scripted REST API that NowLink creates on the instance to bridge HTTP
# calls into ServiceNow's server-side FlowAPI. Without this bridge there is
# no way to trigger a Flow Designer subflow from outside ServiceNow via REST.
#
# Created once via `nowlink setup-flows`. Idempotent — safe to run again.

BRIDGE_SERVICE_ID = "nowlink_flow_bridge"
BRIDGE_NAMESPACE = "x_nowlink"

_BRIDGE_SUBFLOW_SCRIPT = r"""(function process(/*RESTAPIRequest*/ request, /*RESTAPIResponse*/ response) {
    var body = request.body.data;
    var subflowName = body.subflow_name;
    var inputs = body.inputs || {};

    if (!subflowName) {
        response.setStatus(400);
        response.setBody({ error: 'subflow_name is required' });
        return;
    }

    try {
        var runner = sn_fd.FlowAPI.getRunner()
            .subflow(subflowName)
            .inBackground()
            .withInputs(inputs)
            .run();

        var executionId = null;
        if (runner && typeof runner.getExecutionId === 'function') {
            executionId = runner.getExecutionId();
        } else if (runner && typeof runner.getContextId === 'function') {
            executionId = runner.getContextId();
        }

        response.setStatus(200);
        response.setBody({
            status: 'triggered',
            subflow_name: subflowName,
            execution_id: executionId,
        });
    } catch (e) {
        response.setStatus(500);
        response.setBody({ error: e.message || String(e) });
    }
})(request, response);"""

_BRIDGE_FLOW_SCRIPT = r"""(function process(/*RESTAPIRequest*/ request, /*RESTAPIResponse*/ response) {
    var body = request.body.data;
    var flowName = body.flow_name;
    var tableName = body.table_name;
    var sysId = body.sys_id;

    if (!flowName) {
        response.setStatus(400);
        response.setBody({ error: 'flow_name is required' });
        return;
    }

    try {
        var inputs = {};
        if (tableName && sysId) {
            var gr = new GlideRecord(tableName);
            if (gr.get(sysId)) {
                inputs['current'] = gr;
                inputs['table_name'] = tableName;
            } else {
                response.setStatus(404);
                response.setBody({ error: 'Record not found: ' + tableName + '/' + sysId });
                return;
            }
        }

        var runner = sn_fd.FlowAPI.getRunner()
            .flow(flowName)
            .inBackground()
            .withInputs(inputs)
            .run();

        var executionId = null;
        if (runner && typeof runner.getExecutionId === 'function') {
            executionId = runner.getExecutionId();
        } else if (runner && typeof runner.getContextId === 'function') {
            executionId = runner.getContextId();
        }

        response.setStatus(200);
        response.setBody({
            status: 'triggered',
            flow_name: flowName,
            execution_id: executionId,
        });
    } catch (e) {
        response.setStatus(500);
        response.setBody({ error: e.message || String(e) });
    }
})(request, response);"""

_BRIDGE_ACTION_SCRIPT = r"""(function process(/*RESTAPIRequest*/ request, /*RESTAPIResponse*/ response) {
    var body = request.body.data;
    var actionName = body.action_name;
    var inputs = body.inputs || {};

    if (!actionName) {
        response.setStatus(400);
        response.setBody({ error: 'action_name is required' });
        return;
    }

    try {
        var runner = sn_fd.FlowAPI.getRunner()
            .action(actionName)
            .inBackground()
            .withInputs(inputs)
            .run();

        var executionId = null;
        if (runner && typeof runner.getExecutionId === 'function') {
            executionId = runner.getExecutionId();
        } else if (runner && typeof runner.getContextId === 'function') {
            executionId = runner.getContextId();
        }

        response.setStatus(200);
        response.setBody({
            status: 'triggered',
            action_name: actionName,
            execution_id: executionId,
        });
    } catch (e) {
        response.setStatus(500);
        response.setBody({ error: e.message || String(e) });
    }
})(request, response);"""

_BRIDGE_OPERATIONS = [
    {
        "name": "Trigger Subflow",
        "relative_path": "/trigger-subflow",
        "script": _BRIDGE_SUBFLOW_SCRIPT,
        "description": "Trigger a Flow Designer subflow by name with inputs.",
    },
    {
        "name": "Trigger Flow",
        "relative_path": "/trigger-flow",
        "script": _BRIDGE_FLOW_SCRIPT,
        "description": "Trigger a Flow Designer flow by name with an optional record context.",
    },
    {
        "name": "Trigger Action",
        "relative_path": "/trigger-action",
        "script": _BRIDGE_ACTION_SCRIPT,
        "description": "Trigger a Flow Designer action by name with inputs.",
    },
]


def setup_flow_bridge() -> dict:
    """
    Create the NowLink Flow Bridge Scripted REST API on the instance if it does
    not already exist. Idempotent — safe to call on every `nowlink setup-flows`.

    Creates three endpoints under /api/x_nowlink/nowlink_flow_bridge/:
        POST /trigger-subflow  — trigger a subflow by scope.internal_name + inputs
        POST /trigger-flow     — trigger a flow by scope.internal_name + record context
        POST /trigger-action   — trigger an action by scope.internal_name + inputs

    All three call sn_fd.FlowAPI server-side and return an execution_id.
    Requires nowlink.dev to have the web_service_admin role.

    Returns a dict with keys:
        created: bool  — True if any new records were created, False if fully installed
        bridge_url: str — base URL of the bridge
        message: str
    """
    base_url = _get_base_url()
    bridge_url = f"{base_url}/api/{BRIDGE_NAMESPACE}/{BRIDGE_SERVICE_ID}"

    with httpx.Client(verify=False) as client:

        # ── Step 1: check if definition exists ────────────────────────────────
        r = client.get(
            f"{base_url}/api/now/table/sys_ws_definition",
            headers=_auth_headers(),
            params={
                "sysparm_query": f"service_id={BRIDGE_SERVICE_ID}",
                "sysparm_fields": "sys_id,name",
                "sysparm_limit": "1",
            },
            timeout=REQUEST_TIMEOUT,
        )
        existing = _handle_response(r, "check flow bridge definition").get("result", [])

        if existing:
            defn_sys_id = existing[0]["sys_id"]
        else:
            # ── Step 2: create the definition ─────────────────────────────────
            defn_sys_id = None
            try:
                r2 = client.post(
                    f"{base_url}/api/now/table/sys_ws_definition",
                    headers=_auth_headers(),
                    json={
                        "name": "NowLink Flow Bridge",
                        "service_id": BRIDGE_SERVICE_ID,
                        "namespace": BRIDGE_NAMESPACE,
                        "is_active": "true",
                        "short_description": "NowLink bridge for triggering Flow Designer subflows, flows, and actions via REST.",
                    },
                    timeout=REQUEST_TIMEOUT,
                )
                defn = _handle_response(r2, "create flow bridge definition").get("result", {})
                defn_sys_id = defn.get("sys_id")
            except httpx.TimeoutException:
                with httpx.Client(verify=False) as verify_client:
                    r_check = verify_client.get(
                        f"{base_url}/api/now/table/sys_ws_definition",
                        headers=_auth_headers(),
                        params={
                            "sysparm_query": f"service_id={BRIDGE_SERVICE_ID}",
                            "sysparm_fields": "sys_id",
                            "sysparm_limit": "1",
                        },
                        timeout=REQUEST_TIMEOUT,
                    )
                    found = _handle_response(r_check, "post-timeout check for bridge definition").get("result", [])
                    if not found:
                        raise RuntimeError(
                            "ServiceNow did not respond in time and the flow bridge definition "
                            "was not created. Run `nowlink setup-flows` again to retry."
                        )
                    defn_sys_id = found[0]["sys_id"]

            if not defn_sys_id:
                raise RuntimeError("Flow bridge definition created but no sys_id returned.")

        # ── Step 3: check existing operations and create any missing ──────────
        r_ops = client.get(
            f"{base_url}/api/now/table/sys_ws_operation",
            headers=_auth_headers(),
            params={
                "sysparm_query": f"web_service_definition={defn_sys_id}",
                "sysparm_fields": "relative_path",
                "sysparm_limit": "10",
            },
            timeout=REQUEST_TIMEOUT,
        )
        existing_paths = {
            op["relative_path"]
            for op in _handle_response(r_ops, "list bridge operations").get("result", [])
        }

        # Return early if all operations already exist
        needed = [op for op in _BRIDGE_OPERATIONS if op["relative_path"] not in existing_paths]
        if not needed:
            return {
                "created": False,
                "bridge_url": bridge_url,
                "message": "Flow bridge already installed.",
            }

        # Create missing operations
        for op in needed:
            try:
                r3 = client.post(
                    f"{base_url}/api/now/table/sys_ws_operation",
                    headers=_auth_headers(),
                    json={
                        "web_service_definition": defn_sys_id,
                        "name": op["name"],
                        "http_method": "POST",
                        "relative_path": op["relative_path"],
                        "operation_script": op["script"],
                        "requires_acl_authorization": "false",
                        "requires_authentication": "true",
                        "short_description": op["description"],
                    },
                    timeout=REQUEST_TIMEOUT,
                )
                _handle_response(r3, f"create bridge operation {op['relative_path']}")
            except httpx.TimeoutException:
                with httpx.Client(verify=False) as verify_client:
                    r_verify = verify_client.get(
                        f"{base_url}/api/now/table/sys_ws_operation",
                        headers=_auth_headers(),
                        params={
                            "sysparm_query": f"web_service_definition={defn_sys_id}^relative_path={op['relative_path']}",
                            "sysparm_fields": "sys_id",
                            "sysparm_limit": "1",
                        },
                        timeout=REQUEST_TIMEOUT,
                    )
                    if not _handle_response(r_verify, f"post-timeout check for {op['relative_path']}").get("result", []):
                        raise RuntimeError(
                            f"ServiceNow did not respond in time and bridge operation {op['relative_path']} "
                            "was not created. Run `nowlink setup-flows` again to retry."
                        )

        return {
            "created": True,
            "bridge_url": bridge_url,
            "message": f"Flow bridge installed successfully ({len(needed)} operation(s) created).",
        }


def trigger_subflow(subflow_name: str, inputs: dict) -> dict:
    """
    Trigger a Flow Designer subflow by its internal name via the NowLink Flow Bridge.

    subflow_name must be in the format 'scope.internal_name', e.g.:
        'global.nowlink_test_subflow'
        'x_myapp.onboarding_subflow'

    inputs is a dict of input variable names to values matching the subflow's
    declared input variables. Passing unexpected keys is harmless — ServiceNow
    ignores them. Missing required inputs will cause the subflow to fail at runtime.

    Returns:
        {"status": "triggered", "subflow_name": ..., "execution_id": ...}

    execution_id is the sys_id of the sys_flow_context record. Pass it to
    get_flow_status() to check whether the subflow completed successfully.

    Raises RuntimeError if the bridge is not installed (run `nowlink setup-flows`)
    or if FlowAPI cannot find a subflow with the given name.
    """
    base_url = _get_base_url()
    bridge_url = f"{base_url}/api/{BRIDGE_NAMESPACE}/{BRIDGE_SERVICE_ID}/trigger-subflow"

    with httpx.Client(verify=False) as client:
        r = client.post(
            bridge_url,
            headers=_auth_headers(),
            json={"subflow_name": subflow_name, "inputs": inputs},
            timeout=REQUEST_TIMEOUT,
        )

    data = _handle_response(r, f"trigger subflow {subflow_name}")
    result = data.get("result", {})

    if "error" in result:
        raise RuntimeError(f"FlowAPI error triggering '{subflow_name}': {result['error']}")

    return result


def get_flow_status(execution_id: str) -> dict:
    """
    Check the execution status of a subflow via its sys_flow_context sys_id.

    execution_id is the value returned by trigger_subflow().

    Returns a dict with:
        sys_id:            the execution context sys_id
        name:              subflow display name
        state:             'Complete', 'Running', 'Error', 'Cancelled'
        fault_description: error detail if state is 'Error', else empty string
        output_vars:       output variable values if state is 'Complete'

    Subflows run asynchronously. If state is 'Running', call again after a short
    delay. On PDI, simple subflows typically complete within 2–5 seconds.
    """
    with httpx.Client(verify=False) as client:
        r = client.get(
            f"{_get_base_url()}/api/now/table/sys_flow_context/{execution_id}",
            headers=_auth_headers(),
            params={
                "sysparm_display_value": "true",
                "sysparm_fields": "sys_id,name,state,fault_description,error_message,output_vars",
            },
            timeout=REQUEST_TIMEOUT,
        )
    data = _handle_response(r, f"get flow status for {execution_id}")
    result = data.get("result", {})
    if not result:
        raise RuntimeError(f"No flow context found for execution_id={execution_id}")
    return result


def list_subflows() -> list[dict]:
    """
    Return all active, published Flow Designer subflows visible to nowlink.dev.

    Queries sys_hub_flow filtered to type=subflow — subflows only, not flows.
    Flows require an event trigger and cannot be called via FlowAPI from outside
    ServiceNow. Subflows have no trigger and are the correct unit for programmatic
    execution. Note: both flows and subflows share the sys_hub_flow table —
    the type field distinguishes them.

    Returns a list of dicts with name, sys_id, description, and internal_name.
    internal_name is the value to pass to trigger_subflow() as subflow_name,
    formatted as 'scope.internal_name'.
    """
    params = {
        "sysparm_query": "active=true^status=published^type=subflow^ORDERBYDESCsys_updated_on",
        "sysparm_fields": "name,sys_id,description,internal_name,sys_scope.name",
        "sysparm_limit": "50",
        "sysparm_display_value": "true",
    }
    with httpx.Client(verify=False) as client:
        r = client.get(
            f"{_get_base_url()}/api/now/table/sys_hub_flow",
            headers=_auth_headers(),
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
    data = _handle_response(r, "list subflows")
    results = data.get("result", [])

    subflows = []
    for rec in results:
        scope = rec.get("sys_scope.name", "global")
        internal = rec.get("internal_name", "")
        subflows.append({
            "name": rec.get("name", ""),
            "sys_id": rec.get("sys_id", ""),
            "description": rec.get("description", ""),
            "internal_name": internal,
            "trigger_name": f"{scope}.{internal}" if internal else None,
        })
    return subflows


def get_action_inputs(action_name: str) -> list[dict]:
    """
    Return the declared input variables for a Flow Designer action by parsing
    the label_cache field on sys_hub_action_type_definition.

    action_name is in 'scope.internal_name' format, e.g.:
        'global.delete_related_entry_cis_for_task'

    Requires nowlink.dev to have the flow_designer role — sys_hub_action_type_definition
    is gated by a row-level ACL that allows only flow_designer and admin. This is
    different from subflows (sys_hub_flow is readable with rest_service).

    Action inputs are identified in label_cache by type == "action" and name format
    {{action.variable_name}}. Only top-level inputs are returned — nested paths like
    {{action.texts.Text}} are sub-elements of structured inputs and are excluded.
    Inputs with empty names ({{action._}}) are also excluded.

    Returns a list of dicts, one per top-level input:
      [{"name": "task", "label": "Task", "type": "reference", "base_type": "reference"}]

    Returns [] if the action has no inputs or label_cache is missing/unparseable.
    Raises RuntimeError if the action cannot be found.
    """
    import json as _json
    import re

    if "." not in action_name:
        raise RuntimeError(
            f"Invalid action_name format '{action_name}'. "
            "Expected 'scope.internal_name', e.g. 'global.delete_related_entry_cis_for_task'."
        )
    scope_name, internal_name = action_name.split(".", 1)

    params = {
        "sysparm_query": f"internal_name={internal_name}^active=true^state=published",
        "sysparm_fields": "sys_id,name,internal_name,label_cache,sys_scope.name",
        "sysparm_limit": "1",
        "sysparm_display_value": "true",
    }
    with httpx.Client(verify=False) as client:
        r = client.get(
            f"{_get_base_url()}/api/now/table/sys_hub_action_type_definition",
            headers=_auth_headers(),
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
    data = _handle_response(r, f"get_action_inputs for {action_name}")
    results = data.get("result", [])

    if not results:
        raise RuntimeError(
            f"Action '{action_name}' not found. "
            "Use list_actions to see available actions and their trigger_names."
        )

    label_cache_raw = results[0].get("label_cache", "")
    if not label_cache_raw:
        return []

    try:
        all_vars = _json.loads(label_cache_raw)
    except (_json.JSONDecodeError, TypeError):
        return []

    inputs = []
    seen_names = set()
    for var in all_vars:
        if var.get("type") != "action":
            continue  # skip step outputs

        raw_name = var.get("name", "")
        # Extract variable name from {{action.X}} — must match exactly
        match = re.match(r"^\{\{action\.([^}]+)\}\}$", raw_name)
        if not match:
            continue

        var_name = match.group(1)

        # Skip empty names and nested paths (contain a dot)
        if not var_name or var_name == "_" or "." in var_name:
            continue

        # Deduplicate — some actions have case variants of the same name
        if var_name.lower() in seen_names:
            continue
        seen_names.add(var_name.lower())

        label_raw = var.get("label", "")
        # label format is "action➛Display Name" — strip the prefix
        label = label_raw.split("➛", 1)[-1].strip() if "➛" in label_raw else label_raw

        entry = {
            "name": var_name,
            "label": label,
            "type": var.get("base_type", "string"),
        }

        choices = var.get("choices")
        if choices:
            entry["choices"] = [{"label": c["label"], "value": c["value"]} for c in choices]

        inputs.append(entry)

    return inputs


def list_actions(limit: int = 50) -> list[dict]:
    """
    Return active, published Flow Designer actions visible to nowlink.dev.

    Requires nowlink.dev to have the flow_designer role.

    Excludes Integration Hub actions (ih_action=true) — those require paid
    Integration Hub Enterprise and are not triggerable on standard PDIs.

    Returns a list of dicts with name, sys_id, description, internal_name,
    category, and trigger_name (scope.internal_name format).
    """
    params = {
        "sysparm_query": "active=true^state=published^ih_action=false^ORDERBYname",
        "sysparm_fields": "name,sys_id,description,internal_name,category,annotation,sys_scope.name",
        "sysparm_limit": str(min(limit, 200)),
        "sysparm_display_value": "true",
    }
    with httpx.Client(verify=False) as client:
        r = client.get(
            f"{_get_base_url()}/api/now/table/sys_hub_action_type_definition",
            headers=_auth_headers(),
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
    data = _handle_response(r, "list_actions")
    results = data.get("result", [])

    actions = []
    for rec in results:
        scope = rec.get("sys_scope.name", "global")
        internal = rec.get("internal_name", "")
        description = rec.get("description") or rec.get("annotation") or ""
        actions.append({
            "name": rec.get("name", ""),
            "sys_id": rec.get("sys_id", ""),
            "description": description,
            "internal_name": internal,
            "category": rec.get("category", ""),
            "trigger_name": f"{scope}.{internal}" if internal else None,
        })
    return actions


def trigger_action(action_name: str, inputs: dict) -> dict:
    """
    Trigger a Flow Designer action by its internal name via the NowLink Flow Bridge.

    action_name must be in 'scope.internal_name' format, e.g.:
        'global.delete_related_entry_cis_for_task'

    inputs is a dict of {variable_name: value} matching the action's declared inputs.
    Use describe_action to find the correct variable names before triggering.

    Returns:
        {"status": "triggered", "action_name": ..., "execution_id": ...}

    Raises RuntimeError if the bridge is not installed or the action cannot be found.
    """
    base_url = _get_base_url()
    bridge_url = f"{base_url}/api/{BRIDGE_NAMESPACE}/{BRIDGE_SERVICE_ID}/trigger-action"

    with httpx.Client(verify=False) as client:
        r = client.post(
            bridge_url,
            headers=_auth_headers(),
            json={"action_name": action_name, "inputs": inputs},
            timeout=REQUEST_TIMEOUT,
        )

    data = _handle_response(r, f"trigger action {action_name}")
    result = data.get("result", {})

    if "error" in result:
        raise RuntimeError(f"FlowAPI error triggering action '{action_name}': {result['error']}")

    return result


def list_flows(limit: int = 50) -> list[dict]:
    """
    Return all active, published Flow Designer flows visible to nowlink.dev.

    Queries sys_hub_flow filtered to type=flow — flows only, not subflows.
    Flows are triggered by platform events (record changes, schedules, catalog
    items) and cannot be called with arbitrary inputs via the REST API.
    Use list_subflows for programmatically triggerable automation.

    Returns a list of dicts with name, sys_id, description, internal_name,
    and trigger_name (scope.internal_name format).
    """
    params = {
        "sysparm_query": "active=true^status=published^type=flow^ORDERBYname",
        "sysparm_fields": "name,sys_id,description,internal_name,sys_scope.name",
        "sysparm_limit": str(min(limit, 200)),
        "sysparm_display_value": "true",
    }
    with httpx.Client(verify=False) as client:
        r = client.get(
            f"{_get_base_url()}/api/now/table/sys_hub_flow",
            headers=_auth_headers(),
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
    data = _handle_response(r, "list_flows")
    results = data.get("result", [])

    flows = []
    for rec in results:
        scope = rec.get("sys_scope.name", "global")
        internal = rec.get("internal_name", "")
        flows.append({
            "name": rec.get("name", ""),
            "sys_id": rec.get("sys_id", ""),
            "description": rec.get("description", ""),
            "internal_name": internal,
            "trigger_name": f"{scope}.{internal}" if internal else None,
        })
    return flows


def describe_flow(flow_name: str) -> dict:
    """
    Return trigger information for a Flow Designer flow.

    flow_name is in 'scope.internal_name' format, e.g.:
        'global.sla_notification_and_escalation_flow'

    Parses the label_cache field on sys_hub_flow to extract trigger context
    variables — those with label starting with "Trigger➛". These reveal what
    platform event fires the flow and what record/table context it expects.

    Returns:
        {
            "name": display name,
            "description": flow description,
            "trigger_context": [
                {"name": "task_sla_record", "label": "Task SLA Record",
                 "type": "reference", "table": "task_sla"},
                ...
            ],
            "can_trigger_via_api": False,
            "trigger_explanation": "human-readable explanation of what fires this flow"
        }

    can_trigger_via_api is always False — flows require a platform event trigger.
    Use list_subflows for programmatically triggerable automation instead.
    """
    import json as _json

    if "." not in flow_name:
        raise RuntimeError(
            f"Invalid flow_name format '{flow_name}'. "
            "Expected 'scope.internal_name', e.g. 'global.sla_notification_and_escalation_flow'."
        )
    _, internal_name = flow_name.split(".", 1)

    params = {
        "sysparm_query": f"internal_name={internal_name}^type=flow^active=true",
        "sysparm_fields": "sys_id,name,description,internal_name,label_cache",
        "sysparm_limit": "1",
        "sysparm_display_value": "true",
    }
    with httpx.Client(verify=False) as client:
        r = client.get(
            f"{_get_base_url()}/api/now/table/sys_hub_flow",
            headers=_auth_headers(),
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
    data = _handle_response(r, f"describe_flow for {flow_name}")
    results = data.get("result", [])

    if not results:
        raise RuntimeError(
            f"Flow '{flow_name}' not found. "
            "Use list_flows to see available flows and their trigger_names."
        )

    rec = results[0]
    label_cache_raw = rec.get("label_cache", "")

    trigger_context = []
    if label_cache_raw:
        try:
            all_vars = _json.loads(label_cache_raw)
            for var in all_vars:
                label_raw = var.get("label", "")
                if not label_raw.startswith("Trigger"):
                    continue
                if "➛" not in label_raw:
                    continue
                # Only top-level trigger vars — label has exactly one ➛
                # Handles both "Trigger➛Record" and "Trigger - Record Updated➛Record" formats
                if label_raw.count("➛") != 1:
                    continue
                label = label_raw.split("➛", 1)[-1].strip()
                if not label:
                    continue
                entry = {
                    "name": var.get("name", "").split(".", 1)[-1] if "." in var.get("name", "") else var.get("name", ""),
                    "label": label,
                    "type": var.get("base_type", var.get("type", "string")),
                }
                if var.get("reference"):
                    entry["table"] = var["reference"]
                trigger_context.append(entry)
        except (_json.JSONDecodeError, TypeError):
            pass

    # Build a plain-English explanation from the trigger context
    if trigger_context:
        tables = list({e["table"] for e in trigger_context if e.get("table")})
        if tables:
            explanation = (
                f"This flow is triggered by a platform event on the {', '.join(tables)} table(s). "
                "It fires automatically when ServiceNow detects the configured condition "
                "(record created/updated, schedule, etc.) — it cannot be called directly via API. "
                "To trigger equivalent logic on demand, rebuild it as a Subflow in Flow Designer."
            )
        else:
            explanation = (
                "This flow is triggered by a platform event (schedule, application trigger, or similar). "
                "It cannot be called directly via API. "
                "To trigger equivalent logic on demand, rebuild it as a Subflow in Flow Designer."
            )
    else:
        explanation = (
            "Trigger information could not be determined from the flow definition. "
            "Flows cannot be called directly via API — they require a platform event to fire. "
            "To trigger equivalent logic on demand, rebuild it as a Subflow in Flow Designer."
        )

    return {
        "name": rec.get("name", ""),
        "description": rec.get("description", ""),
        "trigger_context": trigger_context,
        "can_trigger_via_api": False,
        "trigger_explanation": explanation,
    }


def get_subflow_inputs(subflow_name: str) -> list[dict]:
    """
    Return the declared input variables for a subflow by parsing the label_cache field
    on sys_hub_flow.

    subflow_name is the trigger_name format: 'scope.internal_name'
    e.g. 'global.nowlink_test_subflow'

    ServiceNow does not expose a clean input-variable REST endpoint accessible with
    standard roles. sys_hub_flow_input returns 403 for nowlink.dev. The label_cache
    field on sys_hub_flow is a JSON array of ALL variables used in the flow — inputs,
    intermediate step outputs, and loop variables. Input variables are identified by
    name starting with "subflow." — all others are intermediate.

    Input variable name to pass to trigger_subflow() is the part after "subflow.":
      "subflow.message" → input name "message"

    Returns a list of dicts, one per declared input:
      [{"name": "message", "label": "Message", "type": "string", "choices": [...]}]

    choices is only present for "choice" type inputs — it's the list of valid values.
    There is no reliable mandatory flag in label_cache — omitted from results.

    Returns [] if the subflow has no inputs or if label_cache is missing/unparseable.
    Raises RuntimeError if the subflow cannot be found.
    """
    import json as _json

    # Parse scope and internal_name from "scope.internal_name"
    if "." not in subflow_name:
        raise RuntimeError(
            f"Invalid subflow_name format '{subflow_name}'. "
            "Expected 'scope.internal_name', e.g. 'global.nowlink_test_subflow'."
        )
    parts = subflow_name.split(".", 1)
    scope_name, internal_name = parts[0], parts[1]

    params = {
        "sysparm_query": f"internal_name={internal_name}^type=subflow^active=true",
        "sysparm_fields": "sys_id,name,internal_name,label_cache",
        "sysparm_limit": "1",
        "sysparm_display_value": "true",
    }
    with httpx.Client(verify=False) as client:
        r = client.get(
            f"{_get_base_url()}/api/now/table/sys_hub_flow",
            headers=_auth_headers(),
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
    data = _handle_response(r, f"get_subflow_inputs for {subflow_name}")
    results = data.get("result", [])

    if not results:
        raise RuntimeError(
            f"Subflow '{subflow_name}' not found. "
            "Use list_subflows to see available subflows and their trigger_names."
        )

    label_cache_raw = results[0].get("label_cache", "")
    if not label_cache_raw:
        return []

    try:
        all_vars = _json.loads(label_cache_raw)
    except (_json.JSONDecodeError, TypeError):
        return []

    inputs = []
    for var in all_vars:
        name = var.get("name", "")
        if not name.startswith("subflow."):
            continue  # skip intermediate step variables

        input_name = name[len("subflow."):]  # strip "subflow." prefix
        label_raw = var.get("label", "")
        # label format is "Input➛Display Name" — strip the prefix
        label = label_raw.split("➛", 1)[-1].strip() if "➛" in label_raw else label_raw

        entry = {
            "name": input_name,
            "label": label,
            "type": var.get("type", "string"),
        }

        # Include choices for choice-type inputs — these are the only valid values
        choices = var.get("choices")
        if choices:
            entry["choices"] = [{"label": c["label"], "value": c["value"]} for c in choices]

        inputs.append(entry)

    return inputs


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
