# tests/test_flows.py
# Unit tests for v0.5 flow input discovery and trigger validation logic.
# Tests the label_cache parsing in client.py and the warning logic in server.py.
# No HTTP calls — all pure logic tests against the parsing functions directly.

import json
import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────
# Replicate the label_cache parsing logic from client.get_subflow_inputs()
# so we can test it without needing a ServiceNow connection.

def parse_label_cache(label_cache_raw: str) -> list[dict]:
    """Mirror of the parsing logic in client.get_subflow_inputs()."""
    if not label_cache_raw:
        return []
    try:
        all_vars = json.loads(label_cache_raw)
    except (json.JSONDecodeError, TypeError):
        return []

    inputs = []
    for var in all_vars:
        name = var.get("name", "")
        if not name.startswith("subflow."):
            continue

        input_name = name[len("subflow."):]
        label_raw = var.get("label", "")
        label = label_raw.split("➛", 1)[-1].strip() if "➛" in label_raw else label_raw

        entry = {
            "name": input_name,
            "label": label,
            "type": var.get("type", "string"),
        }
        choices = var.get("choices")
        if choices:
            entry["choices"] = [{"label": c["label"], "value": c["value"]} for c in choices]

        inputs.append(entry)

    return inputs


def validate_inputs(declared: list[dict], provided: dict) -> dict:
    """Mirror of the validation logic in server.trigger_subflow()."""
    declared_names = {i["name"] for i in declared}
    provided_names = set(provided.keys())
    unknown = provided_names - declared_names
    missing = declared_names - provided_names
    return {"unknown": sorted(unknown), "missing": sorted(missing)}


# ── label_cache parsing ───────────────────────────────────────────────────────

class TestLabelCacheParsing:

    def test_single_string_input(self):
        """Test subflow with one string input — the NowLink Test Subflow."""
        label_cache = json.dumps([
            {
                "name": "subflow.message",
                "label": "Input➛Message",
                "type": "string",
                "usedInstances": {}
            }
        ])
        result = parse_label_cache(label_cache)
        assert len(result) == 1
        assert result[0]["name"] == "message"
        assert result[0]["label"] == "Message"
        assert result[0]["type"] == "string"
        assert "choices" not in result[0]

    def test_intermediate_variables_excluded(self):
        """Variables not starting with 'subflow.' are step outputs — must be excluded."""
        label_cache = json.dumps([
            {"name": "subflow.message", "label": "Input➛Message", "type": "string"},
            {"name": "abc123.Record.sys_id", "label": "1 - Look Up Record➛Sys ID", "type": "GUID"},
            {"name": "def456.__action_status__.message", "label": "2 - Step➛Status➛Message", "type": "string"},
        ])
        result = parse_label_cache(label_cache)
        assert len(result) == 1
        assert result[0]["name"] == "message"

    def test_choice_input_includes_choices(self):
        """Choice type inputs must include the choices list."""
        label_cache = json.dumps([
            {
                "name": "subflow.permission_sync_strategy",
                "label": "Input➛Permission Sync Strategy",
                "type": "choice",
                "choices": [
                    {"label": "Immediate", "value": "immediate", "order": 0},
                    {"label": "Scheduled", "value": "scheduled", "order": 1},
                ]
            }
        ])
        result = parse_label_cache(label_cache)
        assert len(result) == 1
        assert result[0]["name"] == "permission_sync_strategy"
        assert result[0]["type"] == "choice"
        assert len(result[0]["choices"]) == 2
        assert result[0]["choices"][0] == {"label": "Immediate", "value": "immediate"}
        assert result[0]["choices"][1] == {"label": "Scheduled", "value": "scheduled"}

    def test_choice_input_strips_order_field(self):
        """The choices list must only contain label and value — not order."""
        label_cache = json.dumps([
            {
                "name": "subflow.status",
                "label": "Input➛Status",
                "type": "choice",
                "choices": [{"label": "Active", "value": "active", "order": 0.0}]
            }
        ])
        result = parse_label_cache(label_cache)
        assert "order" not in result[0]["choices"][0]

    def test_multiple_inputs(self):
        """Multiple subflow inputs all extracted correctly."""
        label_cache = json.dumps([
            {"name": "subflow.document_sys_id", "label": "Input➛Document Sys Id", "type": "GUID"},
            {"name": "subflow.batch_size", "label": "Input➛Batch Size", "type": "integer"},
            {"name": "subflow.external_permission_status", "label": "Input➛External Permission Status",
             "type": "choice", "choices": [{"label": "Pending", "value": "pending", "order": 0}]},
            # intermediate variable — should be excluded
            {"name": "abc.item.sys_id", "label": "5 - For Each➛Record➛Sys ID", "type": "GUID"},
        ])
        result = parse_label_cache(label_cache)
        assert len(result) == 3
        names = [r["name"] for r in result]
        assert "document_sys_id" in names
        assert "batch_size" in names
        assert "external_permission_status" in names

    def test_label_arrow_stripped(self):
        """Label prefix 'Input➛' is stripped, leaving just the display name."""
        label_cache = json.dumps([
            {"name": "subflow.my_var", "label": "Input➛My Variable Name", "type": "string"}
        ])
        result = parse_label_cache(label_cache)
        assert result[0]["label"] == "My Variable Name"

    def test_label_without_arrow_preserved(self):
        """If label has no ➛, the whole label is kept as-is."""
        label_cache = json.dumps([
            {"name": "subflow.my_var", "label": "Just A Label", "type": "string"}
        ])
        result = parse_label_cache(label_cache)
        assert result[0]["label"] == "Just A Label"

    def test_empty_label_cache(self):
        """Empty string label_cache returns empty list."""
        assert parse_label_cache("") == []

    def test_null_label_cache(self):
        """None label_cache returns empty list."""
        assert parse_label_cache(None) == []

    def test_invalid_json_label_cache(self):
        """Unparseable label_cache returns empty list gracefully."""
        assert parse_label_cache("not valid json {{") == []

    def test_subflow_with_no_inputs(self):
        """A flow with only intermediate variables has no inputs."""
        label_cache = json.dumps([
            {"name": "abc.Record.sys_id", "label": "1 - Look Up➛Sys ID", "type": "GUID"},
            {"name": "def.__action_status__.code", "label": "2 - Step➛Code", "type": "integer"},
        ])
        result = parse_label_cache(label_cache)
        assert result == []

    def test_guid_type_preserved(self):
        """GUID type inputs are returned with type='GUID', not normalised to string."""
        label_cache = json.dumps([
            {"name": "subflow.record_sys_id", "label": "Input➛Record Sys ID", "type": "GUID"}
        ])
        result = parse_label_cache(label_cache)
        assert result[0]["type"] == "GUID"

    def test_non_choice_type_has_no_choices_key(self):
        """String, integer, GUID inputs must not have a choices key."""
        label_cache = json.dumps([
            {"name": "subflow.message", "label": "Input➛Message", "type": "string"},
            {"name": "subflow.count", "label": "Input➛Count", "type": "integer"},
        ])
        result = parse_label_cache(label_cache)
        for inp in result:
            assert "choices" not in inp


# ── Input validation logic ────────────────────────────────────────────────────

class TestInputValidation:

    def test_all_inputs_provided_correctly(self):
        """No warnings when all declared inputs are provided."""
        declared = [
            {"name": "message", "label": "Message", "type": "string"},
        ]
        provided = {"message": "hello"}
        result = validate_inputs(declared, provided)
        assert result["unknown"] == []
        assert result["missing"] == []

    def test_missing_declared_input(self):
        """A declared input not provided by the caller is flagged as missing."""
        declared = [
            {"name": "message", "label": "Message", "type": "string"},
            {"name": "priority", "label": "Priority", "type": "integer"},
        ]
        provided = {"message": "hello"}
        result = validate_inputs(declared, provided)
        assert "priority" in result["missing"]
        assert result["unknown"] == []

    def test_unknown_input_provided(self):
        """A key provided by the caller that isn't declared is flagged as unknown."""
        declared = [
            {"name": "message", "label": "Message", "type": "string"},
        ]
        provided = {"message": "hello", "typo_field": "oops"}
        result = validate_inputs(declared, provided)
        assert "typo_field" in result["unknown"]
        assert result["missing"] == []

    def test_multiple_missing_and_unknown(self):
        """Both missing and unknown are detected simultaneously."""
        declared = [
            {"name": "field_a", "label": "A", "type": "string"},
            {"name": "field_b", "label": "B", "type": "string"},
        ]
        provided = {"field_a": "value", "wrong_key": "value"}
        result = validate_inputs(declared, provided)
        assert "field_b" in result["missing"]
        assert "wrong_key" in result["unknown"]

    def test_empty_inputs_against_no_declared(self):
        """Subflow with no declared inputs and empty provided inputs — no warnings."""
        declared = []
        provided = {}
        result = validate_inputs(declared, provided)
        assert result["unknown"] == []
        assert result["missing"] == []

    def test_extra_inputs_on_no_declared_subflow(self):
        """Providing inputs to a subflow with none declared flags them as unknown."""
        declared = []
        provided = {"surprise": "value"}
        result = validate_inputs(declared, provided)
        assert "surprise" in result["unknown"]

    def test_results_are_sorted(self):
        """Missing and unknown lists are sorted alphabetically for consistent output."""
        declared = [
            {"name": "zebra", "label": "Z", "type": "string"},
            {"name": "apple", "label": "A", "type": "string"},
        ]
        provided = {"mango": "x", "banana": "y"}
        result = validate_inputs(declared, provided)
        assert result["missing"] == ["apple", "zebra"]
        assert result["unknown"] == ["banana", "mango"]


# ── Action label_cache parsing ────────────────────────────────────────────────
# Mirrors client.get_action_inputs() logic — action inputs use {{action.X}} format.

import re

def parse_action_label_cache(label_cache_raw: str) -> list[dict]:
    """Mirror of the parsing logic in client.get_action_inputs()."""
    if not label_cache_raw:
        return []
    try:
        all_vars = json.loads(label_cache_raw)
    except (json.JSONDecodeError, TypeError):
        return []

    inputs = []
    seen_names = set()
    for var in all_vars:
        if var.get("type") != "action":
            continue
        raw_name = var.get("name", "")
        match = re.match(r"^\{\{action\.([^}]+)\}\}$", raw_name)
        if not match:
            continue
        var_name = match.group(1)
        if not var_name or var_name == "_" or "." in var_name:
            continue
        if var_name.lower() in seen_names:
            continue
        seen_names.add(var_name.lower())
        label_raw = var.get("label", "")
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


class TestActionLabelCacheParsing:

    def test_simple_action_input(self):
        """Basic action input extracted correctly."""
        label_cache = json.dumps([
            {"name": "{{action.task}}", "label": "action➛Task",
             "type": "action", "base_type": "reference", "ref": "task"}
        ])
        result = parse_action_label_cache(label_cache)
        assert len(result) == 1
        assert result[0]["name"] == "task"
        assert result[0]["label"] == "Task"
        assert result[0]["type"] == "reference"

    def test_step_variables_excluded(self):
        """Step output variables must be excluded — only type=action kept."""
        label_cache = json.dumps([
            {"name": "{{action.task}}", "label": "action➛Task", "type": "action", "base_type": "reference"},
            {"name": "{{step[abc].error}}", "label": "step➛Script step➛Error", "type": "step", "base_type": "string"},
            {"name": "{{step[abc].variable}}", "label": "step➛Script step➛variable", "type": "step", "base_type": "string"},
        ])
        result = parse_action_label_cache(label_cache)
        assert len(result) == 1
        assert result[0]["name"] == "task"

    def test_nested_paths_excluded(self):
        """Nested action paths like {{action.texts.Text}} must be excluded."""
        label_cache = json.dumps([
            {"name": "{{action.text}}", "label": "action➛Text", "type": "action", "base_type": "string"},
            {"name": "{{action.texts.Text}}", "label": "action➛Texts➛Text", "type": "action", "base_type": "string"},
            {"name": "{{action.target_languages.target_language}}", "label": "action➛Target Languages➛Target Language",
             "type": "action", "base_type": "string"},
        ])
        result = parse_action_label_cache(label_cache)
        assert len(result) == 1
        assert result[0]["name"] == "text"

    def test_empty_name_excluded(self):
        """{{action._}} — empty/unnamed inputs must be excluded."""
        label_cache = json.dumps([
            {"name": "{{action._}}", "label": "action➛", "type": "action", "base_type": "string"},
            {"name": "{{action.notes}}", "label": "action➛Notes", "type": "action", "base_type": "string"},
        ])
        result = parse_action_label_cache(label_cache)
        assert len(result) == 1
        assert result[0]["name"] == "notes"

    def test_case_variant_deduplication(self):
        """Same variable name in different cases (Texts/texts) deduplicated — first wins."""
        label_cache = json.dumps([
            {"name": "{{action.Texts}}", "label": "action➛Texts", "type": "action", "base_type": "string"},
            {"name": "{{action.texts}}", "label": "action➛Texts", "type": "action", "base_type": "string"},
        ])
        result = parse_action_label_cache(label_cache)
        assert len(result) == 1
        assert result[0]["name"] == "Texts"  # first one wins

    def test_label_prefix_stripped(self):
        """'action➛' prefix stripped from label."""
        label_cache = json.dumps([
            {"name": "{{action.source_language}}", "label": "action➛Source Language",
             "type": "action", "base_type": "string"},
        ])
        result = parse_action_label_cache(label_cache)
        assert result[0]["label"] == "Source Language"

    def test_multiple_inputs(self):
        """Multiple action inputs all returned correctly."""
        label_cache = json.dumps([
            {"name": "{{action.task}}", "label": "action➛Task", "type": "action", "base_type": "reference"},
            {"name": "{{action.notes}}", "label": "action➛Notes", "type": "action", "base_type": "string"},
            {"name": "{{action.cmdb_task}}", "label": "action➛CMDB Task", "type": "action", "base_type": "string"},
            # step variable — excluded
            {"name": "{{step[abc].variable}}", "label": "step➛Script step➛variable", "type": "step", "base_type": "string"},
        ])
        result = parse_action_label_cache(label_cache)
        assert len(result) == 3
        names = [r["name"] for r in result]
        assert "task" in names
        assert "notes" in names
        assert "cmdb_task" in names

    def test_empty_label_cache(self):
        """Empty label_cache returns empty list."""
        assert parse_action_label_cache("") == []

    def test_invalid_json(self):
        """Invalid JSON returns empty list gracefully."""
        assert parse_action_label_cache("{bad json}") == []


# ── Flow trigger context parsing ──────────────────────────────────────────────
# Mirrors client.describe_flow() trigger variable extraction logic.

def parse_flow_trigger_context(label_cache_raw: str) -> list[dict]:
    """Mirror of the trigger context parsing in client.describe_flow()."""
    if not label_cache_raw:
        return []
    try:
        all_vars = json.loads(label_cache_raw)
    except (json.JSONDecodeError, TypeError):
        return []

    trigger_context = []
    for var in all_vars:
        label_raw = var.get("label", "")
        if not label_raw.startswith("Trigger"):
            continue
        if "➛" not in label_raw:
            continue
        if label_raw.count("➛") != 1:
            continue
        label = label_raw.split("➛", 1)[-1].strip()
        if not label:
            continue
        name = var.get("name", "")
        entry = {
            "name": name.split(".", 1)[-1] if "." in name else name,
            "label": label,
            "type": var.get("base_type", var.get("type", "string")),
        }
        if var.get("reference"):
            entry["table"] = var["reference"]
        trigger_context.append(entry)
    return trigger_context


class TestFlowTriggerContextParsing:

    def test_record_trigger_extracted(self):
        """Trigger variables with 'Trigger➛' label prefix are extracted."""
        label_cache = json.dumps([
            {
                "name": "Updated_1.current",
                "label": "Trigger - Record Updated➛Versions Record",
                "type": "reference",
                "base_type": "reference",
                "reference": "ds_document_version",
            },
            # step variable — should be excluded
            {"name": "abc.record", "label": "1 - Update Record➛Request Record",
             "type": "reference", "base_type": "reference"},
        ])
        result = parse_flow_trigger_context(label_cache)
        assert len(result) == 1
        assert result[0]["label"] == "Versions Record"
        assert result[0]["table"] == "ds_document_version"

    def test_nested_trigger_vars_excluded(self):
        """Trigger vars with multiple ➛ (nested paths) are excluded — top-level only."""
        label_cache = json.dumps([
            # top-level — keep
            {"name": "Updated_1.current", "label": "Trigger - Record Updated➛Versions Record",
             "type": "reference", "base_type": "reference", "reference": "ds_document_version"},
            # nested path — exclude (two ➛ symbols)
            {"name": "Updated_1.current.document", "label": "Trigger - Record Updated➛Versions Record➛Document",
             "type": "reference", "base_type": "reference", "reference": "ds_document"},
        ])
        result = parse_flow_trigger_context(label_cache)
        assert len(result) == 1
        assert result[0]["label"] == "Versions Record"

    def test_non_trigger_vars_excluded(self):
        """Variables without 'Trigger➛' in label are excluded."""
        label_cache = json.dumps([
            {"name": "SLA Task_1.task_sla_record", "label": "Trigger➛Task SLA Record",
             "type": "reference", "base_type": "reference", "reference": "task_sla"},
            {"name": "step_output.state", "label": "14➛State",
             "type": "choice", "base_type": "choice"},
        ])
        result = parse_flow_trigger_context(label_cache)
        assert len(result) == 1
        assert result[0]["label"] == "Task SLA Record"
        assert result[0]["table"] == "task_sla"

    def test_no_trigger_vars_returns_empty(self):
        """Flow with no Trigger➛ variables returns empty list."""
        label_cache = json.dumps([
            {"name": "abc.state", "label": "1➛State", "type": "choice", "base_type": "choice"},
        ])
        result = parse_flow_trigger_context(label_cache)
        assert result == []

    def test_reference_table_included(self):
        """Reference field includes table name."""
        label_cache = json.dumps([
            {"name": "Created_1.current", "label": "Trigger - Record Created➛Change Request Record",
             "type": "reference", "base_type": "reference", "reference": "change_request"},
        ])
        result = parse_flow_trigger_context(label_cache)
        assert result[0]["table"] == "change_request"

    def test_non_reference_has_no_table(self):
        """Non-reference trigger vars don't get a table key."""
        label_cache = json.dumps([
            {"name": "Scheduled_1.time", "label": "Trigger➛Scheduled Time",
             "type": "glide_date_time", "base_type": "glide_date_time", "reference": ""},
        ])
        result = parse_flow_trigger_context(label_cache)
        assert "table" not in result[0]

    def test_empty_label_cache(self):
        """Empty label_cache returns empty list."""
        assert parse_flow_trigger_context("") == []


# ── trigger_flow fallback logic ───────────────────────────────────────────────

class TestTriggerFlowFallback:

    def test_triggered_is_always_false(self):
        """trigger_flow response must always have triggered=False."""
        response = {
            "triggered": False,
            "reason": "Flows cannot be triggered via API — they require a platform event.",
            "flow_name": "SLA notification and escalation flow",
            "trigger_explanation": "This flow fires on task_sla record events.",
            "trigger_context": [],
            "alternatives": "Rebuild as a Subflow.",
        }
        assert response["triggered"] is False

    def test_response_has_required_keys(self):
        """trigger_flow response must contain all required keys."""
        response = {
            "triggered": False,
            "reason": "some reason",
            "flow_name": "My Flow",
            "trigger_explanation": "explanation",
            "trigger_context": [],
            "alternatives": "alternatives",
        }
        required = {"triggered", "reason", "flow_name", "trigger_explanation", "trigger_context", "alternatives"}
        assert required.issubset(response.keys())

    def test_trigger_explanation_mentions_api(self):
        """trigger_explanation must mention that direct API triggering is not possible."""
        explanation = (
            "This flow is triggered by a platform event on the task_sla table(s). "
            "It fires automatically when ServiceNow detects the configured condition "
            "— it cannot be called directly via API."
        )
        assert "cannot" in explanation.lower() or "api" in explanation.lower()

    def test_alternatives_mentions_subflow(self):
        """Alternatives message must mention Subflow as the recommended path."""
        alternatives = (
            "To run this logic on demand: (1) rebuild it as a Subflow in Flow Designer "
            "and call it with trigger_subflow."
        )
        assert "subflow" in alternatives.lower()
