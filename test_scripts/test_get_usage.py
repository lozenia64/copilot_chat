from __future__ import annotations

from _path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

import argparse
import asyncio
import json

import httpx

from _copilot_live_auth import authenticate_copilot_session

USAGE_URL = "https://api.github.com/copilot_internal/user"
ACCOUNT_LABEL = "free"


async def async_main(args: argparse.Namespace) -> int:
    print(f"Authenticate with the GitHub Copilot {ACCOUNT_LABEL} account you want to inspect.")
    auth_service, session = await authenticate_copilot_session(open_browser=not args.no_browser)

    async with httpx.AsyncClient(timeout=auth_service.http_timeout) as client:
        response = await client.get(
            USAGE_URL,
            headers=auth_service._github_headers(session.github_access_token),
        )

    print("status:", response.status_code)
    print("content-type:", response.headers.get("content-type"))
    print("x-github-request-id:", response.headers.get("x-github-request-id"))
    try:
        payload = response.json()
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    except Exception:
        print(response.text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect Copilot usage payload for a Free account via browser-based GitHub device login.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not auto-open the default browser for device-flow login.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
