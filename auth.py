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


async def _code_login_v4_with_live_agreement(api_client, email: str, code: str):
    """Replicate RoborockApiClient.code_login_v4, but fetch the current
    user-agreement version instead of trusting the library's hardcoded one."""
    import secrets
    import string

    from roborock.data.containers import UserData
    from roborock.web_api import PreparedRequest

    base_url = await api_client.base_url
    country = await api_client.country
    country_code = await api_client.country_code
    header_clientid = api_client._get_header_client_id()

    agreement_request = PreparedRequest(base_url, api_client.session, {"header_clientid": header_clientid})
    agreement_response = await agreement_request.request(
        "get", "/api/v3/app/agreement/latest", params={"country": country or "US"}
    )
    data = agreement_response.get("data", {})

    async def attempt_login(major: int, minor: int) -> dict:
        x_mercy_ks = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
        x_mercy_k = await api_client._sign_key_v3(x_mercy_ks)
        login_request = PreparedRequest(
            base_url,
            api_client.session,
            {
                "header_clientid": header_clientid,
                "x-mercy-ks": x_mercy_ks,
                "x-mercy-k": x_mercy_k,
                "Content-Type": "application/x-www-form-urlencoded",
                "header_clientlang": "en",
                "header_appversion": "4.54.02",
                "header_phonesystem": "iOS",
                "header_phonemodel": "iPhone16,1",
            },
        )
        return await login_request.request(
            "post",
            "/api/v4/auth/email/login/code",
            data={
                "country": country,
                "countryCode": country_code,
                "email": email,
                "code": code,
                "majorVersion": major,
                "minorVersion": minor,
            },
        )

    # Try the live agreement version first, then fall back to the library's
    # hardcoded one in case /agreement/latest ever regresses or changes shape.
    for major, minor in [(data.get("majorVersion", 14), data.get("minorVersion", 0)), (14, 0)]:
        response = await attempt_login(major, minor)
        if response.get("code") == 200:
            return UserData.from_dict(response["data"])

    raise RuntimeError(f"v4 login failed: code={response.get('code')} msg={response.get('msg')}")


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
        # Fallback to v4 login. python-roborock's code_login_v4 hardcodes the
        # user-agreement majorVersion/minorVersion it sends; when Roborock bumps
        # the live agreement (checked via /api/v3/app/agreement/latest), a stale
        # hardcoded version trips response code 3006 (invalid user agreement)
        # even though nothing needs accepting in-app. Try the live version first,
        # falling back to the library's hardcoded one.
        user_data = await _code_login_v4_with_live_agreement(api_client, email, code)

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
