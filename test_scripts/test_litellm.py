from __future__ import annotations

from _path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

import argparse
import asyncio
import json
import os

import litellm

from _copilot_live_auth import authenticate_copilot_session
from services.copilot_headers import build_copilot_headers

DEFAULT_MODEL = os.getenv("COPILOT_TEST_MODEL", "gpt-5.4")


async def async_main(args: argparse.Namespace) -> int:
    _, session = await authenticate_copilot_session(open_browser=not args.no_browser)

    headers = build_copilot_headers()
    headers["X-Initiator"] = "user"

    response = await litellm.acompletion(
        model=args.model,
        messages=[{"role": "user", "content": "hello"}],
        api_key=session.copilot_api_token,
        base_url=session.copilot_api_base,
        extra_headers=headers,
        custom_llm_provider="openai",
        stream=False,
    )

    print(json.dumps(response.model_dump(), indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a live LiteLLM Copilot completion after browser-based GitHub device login.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model to call. Default: {DEFAULT_MODEL}",
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
