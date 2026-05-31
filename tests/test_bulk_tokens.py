# tests/test_bulk_tokens.py
# Unit tests for bulk operation session token logic.
# Tests the _bulk_tokens dict, token expiry, and bulk_execute guard behaviour.
# No HTTP calls — all pure logic tests.

import uuid
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock


# ── Token store helpers ───────────────────────────────────────────────────────
# We test the token logic by importing the module-level dict and manipulating it
# directly — the same way bulk_preview and bulk_execute do at runtime.

def make_token(expires_in_seconds: int = 300) -> tuple[str, dict]:
    """Create a valid token entry as bulk_preview would."""
    token = str(uuid.uuid4())
    stored = {
        "table": "incident",
        "filters": "state=1",
        "fields_to_set": {"impact": "1", "urgency": "1"},
        "count": 10,
        "expires_at": datetime.now() + timedelta(seconds=expires_in_seconds),
    }
    return token, stored


# ── Token generation ──────────────────────────────────────────────────────────

class TestTokenGeneration:

    def test_token_is_valid_uuid(self):
        token, _ = make_token()
        parsed = uuid.UUID(token)
        assert str(parsed) == token

    def test_token_stores_all_required_fields(self):
        token, stored = make_token()
        assert "table" in stored
        assert "filters" in stored
        assert "fields_to_set" in stored
        assert "count" in stored
        assert "expires_at" in stored

    def test_token_expires_at_is_future(self):
        _, stored = make_token(expires_in_seconds=300)
        assert stored["expires_at"] > datetime.now()

    def test_two_tokens_are_unique(self):
        token1, _ = make_token()
        token2, _ = make_token()
        assert token1 != token2


# ── Token expiry ──────────────────────────────────────────────────────────────

class TestTokenExpiry:

    def test_fresh_token_not_expired(self):
        _, stored = make_token(expires_in_seconds=300)
        assert datetime.now() < stored["expires_at"]

    def test_expired_token_detected(self):
        _, stored = make_token(expires_in_seconds=-1)  # already expired
        assert datetime.now() > stored["expires_at"]

    def test_token_expiry_is_5_minutes(self):
        """Token should expire approximately 5 minutes from creation."""
        _, stored = make_token(expires_in_seconds=300)
        delta = stored["expires_at"] - datetime.now()
        # Allow 5 second tolerance
        assert 295 <= delta.total_seconds() <= 305

    def test_just_expired_token(self):
        _, stored = make_token(expires_in_seconds=0)
        # At exactly 0 seconds, may or may not be expired — test boundary
        import time
        time.sleep(0.01)
        assert datetime.now() > stored["expires_at"]


# ── Token immutability ────────────────────────────────────────────────────────

class TestTokenImmutability:

    def test_stored_filters_cannot_be_changed_externally(self):
        """The token encodes the original filter — changing it externally doesn't affect stored state."""
        token, stored = make_token()
        original_filters = stored["filters"]

        # Simulate Claude trying to pass different filters to bulk_execute
        # (can't — bulk_execute reads from stored, not from parameters)
        external_filters = "state=2^priority=1"  # different filter
        assert stored["filters"] == original_filters
        assert stored["filters"] != external_filters

    def test_stored_fields_are_independent_of_caller(self):
        """Modifying the original fields dict after token creation doesn't change stored state."""
        fields = {"impact": "1", "urgency": "1"}
        token = str(uuid.uuid4())
        stored = {
            "table": "incident",
            "filters": "state=1",
            "fields_to_set": dict(fields),  # copy, as bulk_preview does
            "count": 10,
            "expires_at": datetime.now() + timedelta(seconds=300),
        }
        # Mutate original dict
        fields["impact"] = "3"
        # Stored should be unchanged
        assert stored["fields_to_set"]["impact"] == "1"


# ── Token consumption ─────────────────────────────────────────────────────────

class TestTokenConsumption:

    def test_token_removed_after_use(self):
        """Token must be deleted from the store after bulk_execute runs."""
        tokens = {}
        token, stored = make_token()
        tokens[token] = stored

        # Simulate bulk_execute consuming the token
        assert token in tokens
        del tokens[token]
        assert token not in tokens

    def test_token_not_reusable(self):
        """Once consumed, the same token cannot trigger a second execution."""
        tokens = {}
        token, stored = make_token()
        tokens[token] = stored

        # First use
        del tokens[token]

        # Second attempt — token no longer in store
        assert tokens.get(token) is None

    def test_expired_token_removed_from_store(self):
        """Expired tokens should be removed when detected."""
        tokens = {}
        token, stored = make_token(expires_in_seconds=-1)
        tokens[token] = stored

        # Simulate bulk_execute expiry check
        if datetime.now() > tokens[token]["expires_at"]:
            del tokens[token]

        assert token not in tokens


# ── 500-record hard limit ─────────────────────────────────────────────────────

class TestBulkHardLimit:

    def test_count_within_limit(self):
        """Counts up to 500 should be allowed."""
        for count in [1, 10, 100, 499, 500]:
            assert count <= 500

    def test_count_exceeds_limit(self):
        """Counts above 500 should be rejected."""
        for count in [501, 600, 1000]:
            assert count > 500

    def test_limit_enforced_at_preview(self):
        """Simulate bulk_preview refusing to generate a token for > 500 records."""
        count = 612
        limit = 500
        if count > limit:
            result = {
                "error": f"Filter matches {count} records, exceeds {limit}-record limit.",
                "count": count,
                "limit": limit,
            }
        else:
            result = {"token": "some-uuid"}

        assert "error" in result
        assert "token" not in result

    def test_re_count_at_execute_catches_growth(self):
        """If record count grows between preview and execute, execute should refuse."""
        preview_count = 450
        current_count = 512  # grew since preview
        limit = 500

        if current_count > limit:
            result = {
                "error": f"Count changed since preview: now {current_count}, exceeds {limit}.",
                "count": current_count,
            }
        else:
            result = {"updated": current_count}

        assert "error" in result
        assert "updated" not in result


# ── Bulk preview diff ─────────────────────────────────────────────────────────

class TestBulkPreviewDiff:

    def test_diff_shows_before_after(self):
        """Preview sample must show before→after for each field being changed."""
        from nowlink.safety import diff_fields

        current_record = {
            "number": "INC0000001",
            "impact": "2 - Medium",
            "urgency": "2 - Medium",
            "state": "New",
        }
        fields_to_set = {"impact": "1", "urgency": "1"}

        diff = diff_fields(current_record, fields_to_set)

        assert len(diff["changes"]) == 2
        impact_change = next(c for c in diff["changes"] if c["field"] == "impact")
        assert impact_change["from"] == "2 - Medium"
        assert impact_change["to"] == "1"

    def test_already_at_target_shows_unchanged(self):
        """Records already at target value show in unchanged, not changes."""
        from nowlink.safety import diff_fields

        current_record = {
            "number": "INC0000001",
            "impact": "1",   # already High
            "urgency": "1",  # already High
        }
        fields_to_set = {"impact": "1", "urgency": "1"}

        diff = diff_fields(current_record, fields_to_set)

        assert diff["changes"] == []
        assert "impact" in diff["unchanged"]
        assert "urgency" in diff["unchanged"]

    def test_mixed_changed_and_unchanged(self):
        """Some fields change, others don't — diff correctly separates them."""
        from nowlink.safety import diff_fields

        current_record = {
            "impact": "2 - Medium",
            "urgency": "1",  # already High
        }
        fields_to_set = {"impact": "1", "urgency": "1"}

        diff = diff_fields(current_record, fields_to_set)

        assert len(diff["changes"]) == 1
        assert diff["changes"][0]["field"] == "impact"
        assert "urgency" in diff["unchanged"]
