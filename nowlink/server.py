# nowlink/server.py
# FastMCP server definition and tool registration

import httpx
from fastmcp import FastMCP
from nowlink.auth import get_connection_info, get_valid_token, load_credentials

mcp = FastMCP("nowlink")


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

