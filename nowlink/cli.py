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
    console.print("[bold yellow]nowlink connect[/] — not yet implemented")


@app.command()
def serve():
    """Start the NowLink MCP server."""
    console.print("[bold yellow]nowlink serve[/] — not yet implemented")


if __name__ == "__main__":
    app()


