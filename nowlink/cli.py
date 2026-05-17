# nowlink/cli.py
# CLI entry points: init, connect, whoami, serve

import typer
from rich.console import Console

app = typer.Typer(
    name="nowlink",
    help="MCP server for ServiceNow — clean tools, shaped data, safe writes",
    no_args_is_help=True,
)

console = Console()


@app.command()
def init():
    """Set up NowLink credentials for your ServiceNow instance."""
    console.print("[bold yellow]nowlink init[/] — not yet implemented")


@app.command()
def whoami():
    """Confirm connection and show instance details."""
    console.print("[bold yellow]nowlink whoami[/] — not yet implemented")


@app.command()
def connect():
    """Configure Claude Desktop to use NowLink."""
    console.print("[bold yellow]nowlink connect[/] — not yet implemented")


@app.command()
def serve():
    """Start the NowLink MCP server."""
    console.print("[bold yellow]nowlink serve[/] — not yet implemented")


if __name__ == "__main__":
    app()

