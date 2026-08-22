#!/usr/bin/env python3
"""
Roborock Authentication Helper.

Run this script once to authenticate with Roborock and cache your credentials.
The MCP server will use the cached credentials on startup.

Usage:
    python auth.py

Requires ROBOROCK_EMAIL environment variable to be set.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

CACHE_DIR = Path(__file__).parent / ".cache"
CREDENTIALS_FILE = CACHE_DIR / "credentials.json"


async def authenticate():
    from roborock.web_api import RoborockApiClient

    email = os.environ.get("ROBOROCK_EMAIL")
    if not email:
        print("Error: ROBOROCK_EMAIL environment variable not set.")
        sys.exit(1)

    print(f"Authenticating with Roborock for: {email}")

    api_client = RoborockApiClient(username=email)

    # Step 1: Request verification code
    print("Requesting verification code...")
    try:
        await api_client.request_code()
    except Exception:
        # Fallback to v4 code request
        await api_client.request_code_v4()
    print("Verification code sent to your email.")

    # Step 2: Get code from user
    code = input("Enter the verification code: ").strip()
    if not code:
        print("Error: No code entered.")
        sys.exit(1)

    # Step 3: Login with code
    print("Logging in...")
    try:
        user_data = await api_client.code_login(code)
    except Exception:
        from roborock_login import code_login_v4_with_live_agreement

        user_data = await code_login_v4_with_live_agreement(api_client, email, code)

    # Step 4: Get home data (needed for device discovery)
    home_data = await api_client.get_home_data(user_data)

    # Step 5: Cache credentials
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    cache = {
        "email": email,
        "user_data": user_data.as_dict() if hasattr(user_data, "as_dict") else json.loads(json.dumps(user_data, default=str)),
        "home_data": home_data.as_dict() if hasattr(home_data, "as_dict") else json.loads(json.dumps(home_data, default=str)),
        "base_url": await api_client.base_url,
    }

    CREDENTIALS_FILE.write_text(json.dumps(cache, indent=2, default=str))
    print(f"Credentials cached to {CREDENTIALS_FILE}")

    # Show discovered devices
    if hasattr(home_data, "devices") and home_data.devices:
        print("\nDiscovered devices:")
        for device in home_data.devices:
            name = getattr(device, "name", "Unknown")
            duid = getattr(device, "duid", "Unknown")
            model = getattr(device, "model", "Unknown")
            print(f"  - {name} (model: {model}, duid: {duid})")
    elif hasattr(home_data, "rooms") and home_data.rooms:
        print(f"\nFound {len(home_data.rooms)} rooms in home data.")

    print("\nAuthentication complete! You can now start the MCP server.")


if __name__ == "__main__":
    asyncio.run(authenticate())
