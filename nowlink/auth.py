# nowlink/auth.py
# Handles OAuth credentials, token storage and refresh

import time
import httpx
import keyring
from dotenv import load_dotenv, set_key
from pathlib import Path
from rich.console import Console
from rich.prompt import Prompt

load_dotenv()

console = Console()

KEYRING_SERVICE = "nowlink"
ENV_FILE = Path(".env")


# ── Credential storage ────────────────────────────────────────────────────────

def save_credentials(instance_url: str, client_id: str, client_secret: str, username: str, password: str):
    """Store secrets in OS keychain, non-secrets in .env"""
    keyring.set_password(KEYRING_SERVICE, "client_id", client_id)
    keyring.set_password(KEYRING_SERVICE, "client_secret", client_secret)
    keyring.set_password(KEYRING_SERVICE, "username", username)
    keyring.set_password(KEYRING_SERVICE, "password", password)

    # Non-secret config goes in .env
    ENV_FILE.touch(exist_ok=True)
    set_key(str(ENV_FILE), "NOWLINK_INSTANCE_URL", instance_url)
    set_key(str(ENV_FILE), "NOWLINK_PAGE_SIZE", "20")


def load_credentials() -> dict:
    """Load all credentials from keychain and .env"""
    load_dotenv(override=True)

    import os
    instance_url = os.getenv("NOWLINK_INSTANCE_URL")

    client_id = keyring.get_password(KEYRING_SERVICE, "client_id")
    client_secret = keyring.get_password(KEYRING_SERVICE, "client_secret")
    username = keyring.get_password(KEYRING_SERVICE, "username")
    password = keyring.get_password(KEYRING_SERVICE, "password")

    if not all([instance_url, client_id, client_secret, username, password]):
        raise RuntimeError("NowLink is not configured. Run: nowlink init")

    return {
        "instance_url": instance_url.rstrip("/"),
        "client_id": client_id,
        "client_secret": client_secret,
        "username": username,
        "password": password,
    }


# ── Token management ──────────────────────────────────────────────────────────

def fetch_token(creds: dict) -> dict:
    """Request a new OAuth token from ServiceNow"""
    url = f"{creds['instance_url']}/oauth_token.do"

    response = httpx.post(url, data={
        "grant_type": "password",
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "username": creds["username"],
        "password": creds["password"],
    })

    if response.status_code != 200:
        raise RuntimeError(f"Failed to get token: {response.text}")

    token_data = response.json()
    token_data["created_at"] = time.time()

    # Store token in keychain
    import json
    keyring.set_password(KEYRING_SERVICE, "token", json.dumps(token_data))

    return token_data


def token_is_expiring(token_data: dict) -> bool:
    """Return True if token expires within 60 seconds"""
    expires_at = token_data["created_at"] + token_data["expires_in"]
    return time.time() > (expires_at - 60)


def get_valid_token() -> str:
    """Return a valid access token, refreshing proactively if needed"""
    import json

    raw = keyring.get_password(KEYRING_SERVICE, "token")

    if raw:
        token_data = json.loads(raw)
        if not token_is_expiring(token_data):
            return token_data["access_token"]

    # Token missing or expiring — fetch a new one
    creds = load_credentials()
    token_data = fetch_token(creds)
    return token_data["access_token"]


# ── Connection info ───────────────────────────────────────────────────────────

def get_connection_info() -> dict:
    """Return instance URL and username for display purposes"""
    creds = load_credentials()
    return {
        "instance_url": creds["instance_url"],
        "username": creds["username"],
    }


def verify_connection() -> dict:
    """Test connection by calling the ServiceNow whoami endpoint"""
    creds = load_credentials()
    token = get_valid_token()

    response = httpx.get(
        f"{creds['instance_url']}/api/now/table/sys_user",
        headers={"Authorization": f"Bearer {token}"},
        params={
            "sysparm_query": f"user_name={creds['username']}",
            "sysparm_fields": "user_name,name,roles",
            "sysparm_limit": "1",
        }
    )

    if response.status_code == 200:
        results = response.json().get("result", [])
        if results:
            user = results[0]
            return {
                "instance_url": creds["instance_url"],
                "username": user.get("user_name"),
                "display_name": user.get("name"),
                "status": "connected",
            }

    raise RuntimeError(f"Connection verification failed: {response.status_code} {response.text}")

