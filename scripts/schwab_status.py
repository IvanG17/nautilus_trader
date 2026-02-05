#!/usr/bin/env python3
"""
Check Schwab token status.

Usage:
    python scripts/schwab_status.py
    # or
    make schwab-status
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path


def main():
    """Check the status of Schwab tokens."""
    # schwabdev stores tokens in ~/.schwabdev/tokens.db (SQLite)
    token_file = Path.home() / ".schwabdev" / "tokens.db"

    if not token_file.exists():
        print(f"No token file found ({token_file})")
        print("Run: make schwab-auth")
        return 1

    try:
        # Read from SQLite database
        conn = sqlite3.connect(token_file)
        cursor = conn.cursor()
        cursor.execute("SELECT access_token_issued, refresh_token_issued FROM schwabdev LIMIT 1")
        row = cursor.fetchone()
        conn.close()

        if not row:
            print("Token database exists but is empty")
            print("Run: make schwab-auth")
            return 1

        # Parse the ISO format timestamps
        access_issued_str, refresh_issued_str = row
        access_issued = datetime.fromisoformat(access_issued_str.replace("Z", "+00:00"))
        refresh_issued = datetime.fromisoformat(refresh_issued_str.replace("Z", "+00:00"))

        # Make them timezone-naive for comparison
        access_issued = access_issued.replace(tzinfo=None)
        refresh_issued = refresh_issued.replace(tzinfo=None)

        # Refresh tokens are valid for 7 days
        from datetime import timedelta
        refresh_exp = refresh_issued + timedelta(days=7)
        # Access tokens are valid for 30 minutes
        access_exp = access_issued + timedelta(minutes=30)

        tokens = {
            "refresh_token_expires_at": refresh_exp.timestamp(),
            "access_token_expires_at": access_exp.timestamp(),
        }
        refresh_exp = tokens.get("refresh_token_expires_at", 0)
        access_exp = tokens.get("access_token_expires_at", 0)

        now = datetime.now()
        refresh_dt = datetime.fromtimestamp(refresh_exp)
        access_dt = datetime.fromtimestamp(access_exp)

        print("=" * 50)
        print("  Schwab Token Status")
        print("=" * 50)

        # Check refresh token
        if refresh_dt < now:
            print(f"  Refresh Token: EXPIRED on {refresh_dt}")
            print("  Action: Run 'make schwab-auth' to re-authenticate")
            return 1
        else:
            days = (refresh_dt - now).days
            hours = ((refresh_dt - now).seconds // 3600)
            print(f"  Refresh Token: Valid until {refresh_dt}")
            print(f"                 ({days} days, {hours} hours remaining)")

        # Check access token
        if access_dt < now:
            print(f"  Access Token:  EXPIRED (will auto-refresh)")
        else:
            mins = ((access_dt - now).seconds // 60)
            print(f"  Access Token:  Valid for {mins} more minutes")

        print("=" * 50)
        print("  Ready to trade! Run: make schwab-live")
        print("=" * 50)
        return 0

    except Exception as e:
        print(f"Error reading token file: {e}")
        return 1


if __name__ == "__main__":
    exit(main())
