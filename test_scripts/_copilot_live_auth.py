from __future__ import annotations

from _path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

import asyncio
import time
import webbrowser
from typing import Any

from services.copilot_auth import CopilotAuthError, CopilotAuthService, CopilotCredentialSession


async def complete_device_flow_login(
    auth_service: CopilotAuthService,
    *,
    open_browser: bool,
) -> CopilotCredentialSession:
    ticket = await auth_service._request_device_code()
    verification_url = ticket.get("verification_uri_complete") or ticket["verification_uri"]
    interval_seconds = max(int(ticket.get("interval", 5)), 1)
    expires_in_seconds = max(int(ticket.get("expires_in", 900)), 60)

    print("GitHub Copilot device login started.")
    print(f"User code: {ticket['user_code']}")
    print(f"Verification URL: {verification_url}")

    if open_browser:
        opened = webbrowser.open(verification_url)
        if opened:
            print("Opened your default browser for GitHub login.")
        else:
            print("Could not auto-open the browser. Open the URL above manually.")

    deadline = time.monotonic() + expires_in_seconds
    while time.monotonic() < deadline:
        await asyncio.sleep(interval_seconds)
        poll_result = await auth_service._poll_access_token_once(ticket["device_code"])
        status = poll_result["status"]

        if status == "pending":
            interval_seconds = max(interval_seconds + int(poll_result.get("interval_delta", 0)), 1)
            continue
        if status == "denied":
            raise RuntimeError("GitHub login was denied.")
        if status == "expired":
            raise RuntimeError("GitHub login expired before completion.")
        if status == "complete":
            access_token = poll_result.get("access_token")
            if not isinstance(access_token, str) or not access_token:
                raise RuntimeError("GitHub returned a completed login without an access token.")
            session = await auth_service._build_credential_session(access_token)
            print(
                f"Authenticated as GitHub user {session.github_login or '(unknown)'}; "
                f"Copilot token expires at {int(session.copilot_api_expires_at)}."
            )
            return session

        raise RuntimeError(f"Unexpected device flow status: {status}")

    raise RuntimeError("Timed out waiting for GitHub device login to complete.")


async def authenticate_copilot_session(*, open_browser: bool = True) -> tuple[CopilotAuthService, CopilotCredentialSession]:
    auth_service = CopilotAuthService()
    session = await complete_device_flow_login(auth_service, open_browser=open_browser)
    return auth_service, session
