# tests/test_server_structure.py
# Structural tests for server.py tool registration.
# Guards against duplicate @mcp.tool() registrations — a class of bug that
# silently fails at runtime (the last definition wins, earlier ones are lost).
# No HTTP calls, no ServiceNow connection required.

import ast
import pathlib

SERVER_PY = pathlib.Path(__file__).parent.parent / "nowlink" / "server.py"


def _get_tool_decorated_function_names() -> list[str]:
    """
    Parse server.py with ast and return the names of all functions decorated
    with @mcp.tool() in definition order.
    """
    source = SERVER_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)

    names = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for decorator in node.decorator_list:
            # Match @mcp.tool() — ast sees this as a Call on an Attribute
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "mcp"
            ):
                names.append(node.name)
    return names


class TestServerToolRegistration:

    def test_no_duplicate_tool_registrations(self):
        """Every @mcp.tool() function must be defined exactly once."""
        names = _get_tool_decorated_function_names()
        seen = {}
        duplicates = []
        for name in names:
            seen[name] = seen.get(name, 0) + 1
        duplicates = [n for n, count in seen.items() if count > 1]
        assert duplicates == [], (
            f"Duplicate @mcp.tool() registrations found: {duplicates}. "
            "Each tool must be defined exactly once in server.py."
        )

    def test_expected_tools_are_registered(self):
        """All v0.5 tools must be present."""
        names = set(_get_tool_decorated_function_names())
        expected = {
            "ping",
            "query",
            "get_record",
            "describe_table",
            "create_record",
            "update_record",
            "list_subflows",
            "describe_subflow",
            "trigger_subflow",
            "get_flow_status",
            "list_flows",
            "describe_flow",
            "trigger_flow",
            "list_actions",
            "describe_action",
            "trigger_action",
            "bulk_preview",
            "bulk_execute",
            "get_write_log",
        }
        missing = expected - names
        assert missing == set(), f"Expected tools missing from server.py: {missing}"

    def test_tool_count_is_exact(self):
        """Exactly 19 tools registered — no more, no less (v0.5 baseline)."""
        names = _get_tool_decorated_function_names()
        assert len(names) == 19, (
            f"Expected 19 registered tools, found {len(names)}: {names}. "
            "Update this test when adding new tools in v0.6."
        )
