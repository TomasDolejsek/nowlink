# nowlink/cli.py
# CLI entry points: init, connect, whoami, serve

import typer
from rich.console import Console
from rich.prompt import Prompt

app = typer.Typer(
    name="nowlink",
    help="MCP server for ServiceNow — clean tools, shaped data, safe writes",
    no_args_is_help=True,
)

console = Console()


@app.command()
def init():
    """Set up NowLink credentials for your ServiceNow instance."""
    console.print("\n[bold]NowLink Setup[/bold]\n")

    instance_url = Prompt.ask("ServiceNow instance URL (e.g. https://dev123.service-now.com)")
    client_id = Prompt.ask("OAuth Client ID")
    client_secret = Prompt.ask("OAuth Client Secret", password=True)
    username = Prompt.ask("Integration user (e.g. nowlink.dev)")
    password = Prompt.ask("Password", password=True)

    from nowlink.auth import save_credentials, fetch_token, load_credentials
    try:
        save_credentials(instance_url, client_id, client_secret, username, password)
        console.print("\n[yellow]Testing connection...[/yellow]")
        creds = load_credentials()
        fetch_token(creds)
        console.print("[bold green]✓ Connected successfully![/bold green]")
        console.print(f"[dim]Instance: {instance_url}[/dim]")
        console.print("\nRun [bold]nowlink connect[/bold] to configure Claude Desktop.\n")
    except Exception as e:
        console.print(f"[bold red]✗ Setup failed:[/bold red] {e}")
        raise typer.Exit(1)


@app.command()
def whoami():
    """Confirm connection and show instance details."""
    from nowlink.auth import verify_connection
    try:
        info = verify_connection()
        console.print(f"\n[bold green]✓ Connected[/bold green]")
        console.print(f"  Instance:  {info['instance_url']}")
        console.print(f"  User:      {info['display_name']} ({info['username']})\n")
    except Exception as e:
        console.print(f"[bold red]✗ Not connected:[/bold red] {e}")
        raise typer.Exit(1)


@app.command()
def connect():
    """Configure Claude Desktop to use NowLink."""
    import json
    import sys
    from pathlib import Path

    # Locate claude_desktop_config.json
    if sys.platform == "win32":
        config_path = Path.home() / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json"
    else:
        config_path = Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"

    # Auto-detect the nowlink executable path from the current Python environment.
    # Claude Desktop does not inherit the virtual environment PATH, so we must
    # write the absolute path to the executable, not just "nowlink".
    executable = Path(sys.executable)
    if sys.platform == "win32":
        nowlink_exe = executable.parent / "nowlink.exe"
    else:
        nowlink_exe = executable.parent / "nowlink"

    if not nowlink_exe.exists():
        console.print(f"[bold red]✗ Could not find nowlink executable at:[/bold red] {nowlink_exe}")
        console.print("Make sure NowLink is installed in the active virtual environment.")
        raise typer.Exit(1)

    # Read existing config or start fresh
    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)
    else:
        config = {}

    # Merge NowLink in — don't overwrite other MCP servers
    if "mcpServers" not in config:
        config["mcpServers"] = {}

    config["mcpServers"]["nowlink"] = {
        "command": str(nowlink_exe),
        "args": ["serve"]
    }

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)

    console.print(f"\n[bold green]✓ Claude Desktop configured[/bold green]")
    console.print(f"  Executable: {nowlink_exe}")
    console.print(f"  Config: {config_path}")
    console.print("\n[yellow]Restart Claude Desktop to activate NowLink.[/yellow]\n")


@app.command()
def setup_flows():
    """Install the NowLink Flow Bridge on your ServiceNow instance."""
    from nowlink.client import setup_flow_bridge
    import httpx
    console.print("\n[bold]Setting up NowLink Flow Bridge...[/bold]")
    try:
        result = setup_flow_bridge()
        if result["created"]:
            console.print(f"\n[bold green]✓ Flow bridge installed[/bold green]")
        else:
            console.print(f"\n[bold green]✓ Flow bridge already installed[/bold green]")
        console.print(f"  Endpoints: {result['bridge_url']}/trigger-subflow")
        console.print(f"             {result['bridge_url']}/trigger-flow")
        console.print(f"             {result['bridge_url']}/trigger-action")
        console.print(f"\n[yellow]Claude can now trigger Flow Designer subflows.[/yellow]\n")
    except httpx.TimeoutException:
        # PDI timed out — retry silently using idempotency check
        try:
            result = setup_flow_bridge()
            if result["created"]:
                console.print(f"\n[bold green]✓ Flow bridge installed[/bold green]")
            else:
                console.print(f"\n[bold green]✓ Flow bridge already installed[/bold green]")
            console.print(f"  Endpoints: {result['bridge_url']}/trigger-subflow")
            console.print(f"             {result['bridge_url']}/trigger-flow")
            console.print(f"             {result['bridge_url']}/trigger-action")
            console.print(f"\n[yellow]Claude can now trigger Flow Designer subflows.[/yellow]\n")
        except Exception as e2:
            console.print(f"\n[bold red]✗ Setup failed:[/bold red] {e2}\n")
            raise typer.Exit(1)
    except RuntimeError as e:
        msg = str(e)
        console.print(f"\n[bold red]✗ Setup failed:[/bold red] {msg}")
        if "Access denied" in msg or "Unauthorized" in msg:
            console.print("[yellow]Make sure nowlink.dev has the web_service_admin role.[/yellow]")
        console.print()
        raise typer.Exit(1)


@app.command()
def serve():
    """Start the NowLink MCP server."""
    from nowlink.server import mcp
    mcp.run()


if __name__ == "__main__":
    app()


