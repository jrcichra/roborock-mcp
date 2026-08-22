#!/usr/bin/env python3
"""Shared Roborock email-code login helper, used by both auth.py and server.py."""

import secrets
import string


async def code_login_v4_with_live_agreement(api_client, email: str, code: str):
    """Replicate RoborockApiClient.code_login_v4, but fetch the current
    user-agreement version instead of trusting the library's hardcoded one.

    python-roborock's code_login_v4 hardcodes the user-agreement majorVersion/
    minorVersion it sends; when Roborock bumps the live agreement (checked via
    /api/v3/app/agreement/latest), a stale hardcoded version trips response
    code 3006 (invalid user agreement) even though nothing needs accepting
    in-app. This tries the live version first, falling back to the library's
    hardcoded one.
    """
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

    for major, minor in [(data.get("majorVersion", 14), data.get("minorVersion", 0)), (14, 0)]:
        response = await attempt_login(major, minor)
        if response.get("code") == 200:
            return UserData.from_dict(response["data"])

    raise RuntimeError(f"v4 login failed: code={response.get('code')} msg={response.get('msg')}")
