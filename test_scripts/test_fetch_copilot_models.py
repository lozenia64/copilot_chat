from __future__ import annotations

from _path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

import argparse
import asyncio
import json
import re
import sys
from typing import Any

import httpx

from _copilot_live_auth import authenticate_copilot_session
from services.copilot_auth import CopilotCredentialSession
from services.copilot_headers import build_copilot_headers

DEFAULT_INVALID_MODEL = "claude-opus-4.6"
DEFAULT_PROMPT = "Reply with the single word ok."
MODEL_HINT_PATTERN = re.compile(
    r"\b(?:gpt|claude|gemini|llama|mistral|deepseek|o1|o3|o4|codex)[a-z0-9._-]*\b",
    flags=re.IGNORECASE,
)


def _looks_like_model_id(value: str) -> bool:
    normalized = value.strip().strip('"\'`,')
    if not normalized:
        return False
    lower = normalized.lower()
    if len(lower) < 2:
        return False
    if "/" in lower or " " in lower:
        return False
    if lower in {"model", "models", "id", "data", "error"}:
        return False
    return MODEL_HINT_PATTERN.fullmatch(lower) is not None or (
        any(prefix in lower for prefix in ("gpt-", "claude-", "gemini-", "llama-", "mistral-", "deepseek-", "codex"))
        or lower.startswith(("o1", "o3", "o4"))
    )


def _dedupe_sorted(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip().strip('"\'`,')
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return sorted(result, key=str.lower)


def extract_model_ids_from_payload(payload: Any) -> list[str]:
    candidates: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                lowered_key = str(key).lower()
                if lowered_key in {
                    "id",
                    "model",
                    "models",
                    "available_models",
                    "supported_models",
                    "availablemodels",
                    "supportedmodels",
                    "name",
                }:
                    if isinstance(nested, str) and _looks_like_model_id(nested):
                        candidates.append(nested)
                    elif isinstance(nested, list):
                        for item in nested:
                            if isinstance(item, str) and _looks_like_model_id(item):
                                candidates.append(item)
                            elif isinstance(item, dict):
                                for child_key in ("id", "model", "name"):
                                    child_value = item.get(child_key)
                                    if isinstance(child_value, str) and _looks_like_model_id(child_value):
                                        candidates.append(child_value)
                visit(nested)
            return

        if isinstance(value, list):
            for item in value:
                visit(item)
            return

        if isinstance(value, str):
            for match in MODEL_HINT_PATTERN.findall(value):
                if _looks_like_model_id(match):
                    candidates.append(match)

    visit(payload)
    return _dedupe_sorted(candidates)


async def request_invalid_model_error(
    session: CopilotCredentialSession,
    *,
    invalid_model: str,
    prompt: str,
) -> tuple[int, Any]:
    headers = build_copilot_headers()
    headers["authorization"] = f"Bearer {session.copilot_api_token}"
    headers["x-initiator"] = "user"

    payload = {
        "model": invalid_model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        response = await client.post(
            f"{session.copilot_api_base.rstrip('/')}/chat/completions",
            headers=headers,
            json=payload,
        )

    try:
        body: Any = response.json()
    except ValueError:
        body = {"raw_text": response.text}

    return response.status_code, body


async def request_models_endpoint(
    session: CopilotCredentialSession,
) -> tuple[int, Any]:
    headers = build_copilot_headers()
    headers["authorization"] = f"Bearer {session.copilot_api_token}"
    headers["x-initiator"] = "user"

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        response = await client.get(
            f"{session.copilot_api_base.rstrip('/')}/models",
            headers=headers,
        )

    try:
        body: Any = response.json()
    except ValueError:
        body = {"raw_text": response.text}

    return response.status_code, body


async def async_main(args: argparse.Namespace) -> int:
    try:
        _, session = await authenticate_copilot_session(open_browser=not args.no_browser)
    except Exception as exc:
        print(f"Authentication failed: {exc}", file=sys.stderr)
        return 1

    invalid_status, invalid_payload = await request_invalid_model_error(
        session,
        invalid_model=args.invalid_model,
        prompt=args.prompt,
    )
    invalid_models = extract_model_ids_from_payload(invalid_payload)

    result: dict[str, Any] = {
        "invalid_model_probe": {
            "status_code": invalid_status,
            "model": args.invalid_model,
            "payload": invalid_payload,
            "extracted_models": invalid_models,
        }
    }

    if args.include_models_endpoint:
        models_status, models_payload = await request_models_endpoint(session)
        endpoint_models = extract_model_ids_from_payload(models_payload)
        result["models_endpoint"] = {
            "status_code": models_status,
            "payload": models_payload,
            "extracted_models": endpoint_models,
        }

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Authenticate via GitHub device flow, intentionally request an unsupported Copilot model, "
            "and print the returned payload plus any extracted model IDs."
        )
    )
    parser.add_argument(
        "--invalid-model",
        default=DEFAULT_INVALID_MODEL,
        help=f"Model name to intentionally probe with. Default: {DEFAULT_INVALID_MODEL}",
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROMPT,
        help="Prompt text to send with the invalid-model request.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not auto-open the default browser for device-flow login.",
    )
    parser.add_argument(
        "--include-models-endpoint",
        action="store_true",
        help="Also call the Copilot /models endpoint and include its response in the output.",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
