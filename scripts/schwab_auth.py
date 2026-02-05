#!/usr/bin/env python3
"""
Schwab OAuth Authentication Script.

This script handles the OAuth2 authentication flow for the Schwab API.
It will open a browser for authentication and save the tokens locally.

Usage:
    python scripts/schwab_auth.py
    # or
    make schwab-auth
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

def main():
    """Run the Schwab authentication flow."""
    # Get credentials from environment
    app_key = os.environ.get("SCHWAB_APP_KEY")
    app_secret = os.environ.get("SCHWAB_APP_SECRET")
    callback_url = os.environ.get("SCHWAB_CALLBACK_URL", "https://127.0.0.1")

    if not app_key or not app_secret:
        print("ERROR: Missing SCHWAB_APP_KEY or SCHWAB_APP_SECRET")
        print("Set these in your .env file or environment variables")
        sys.exit(1)

    print("=" * 60)
    print("  Schwab OAuth Authentication")
    print("=" * 60)
    print(f"  App Key: {app_key[:10]}...")
    print(f"  Callback URL: {callback_url}")
    print("=" * 60)
    print()

    try:
        import schwabdev

        # Create client - this will trigger the OAuth flow if needed
        # The schwabdev library handles token storage automatically
        client = schwabdev.Client(
            app_key=app_key,
            app_secret=app_secret,
            callback_url=callback_url,
        )

        # Test the connection by getting account info
        print("\nTesting connection...")
        response = client.linked_accounts()

        if response.ok:
            accounts = response.json()
            print(f"\nAuthentication successful!")
            print(f"Found {len(accounts)} linked account(s)")
            for acc in accounts:
                acc_num = acc.get("accountNumber", "N/A")
                print(f"  - Account: {acc_num[:4]}****")
            print("\nTokens have been saved. You can now run:")
            print("  make schwab-live")
        else:
            print(f"\nAuthentication failed: {response.status_code}")
            print(response.text)
            sys.exit(1)

    except Exception as e:
        print(f"\nError during authentication: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
