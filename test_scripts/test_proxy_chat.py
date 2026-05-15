from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("PROXY_CHAT_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
MODEL = os.getenv("PROXY_CHAT_MODEL", "gpt-5.4")
PROMPT = os.getenv("PROXY_CHAT_PROMPT", "안녕! 짧게 자기소개해줘.")
TIMEOUT_SECONDS = float(os.getenv("PROXY_CHAT_TIMEOUT", "120"))


def pretty_print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def main() -> int:
    request_body = {
        "MODEL": MODEL,
        "PROMPT": PROMPT,
    }

    print(f"[request] POST {BASE_URL}/api/proxy/chat")
    print("[request] body:")
    pretty_print_json(request_body)
    print()

    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
            response = client.post(
                f"{BASE_URL}/api/proxy/chat",
                json=request_body,
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError as exc:
        print(f"[error] request failed: {exc}", file=sys.stderr)
        return 1

    print(f"[response] HTTP {response.status_code}")

    refreshed_envelope = response.headers.get("X-Copilot-Credential-Envelope")
    if refreshed_envelope:
        print("[response] X-Copilot-Credential-Envelope header detected")

    try:
        payload = response.json()
    except json.JSONDecodeError:
        print("[response] non-JSON body:")
        print(response.text)
        return 1 if response.status_code >= 400 else 0

    print("[response] body:")
    pretty_print_json(payload)

    if response.status_code >= 400:
        return 1

    content = payload.get("content")
    if isinstance(content, str):
        print()
        print("[assistant]")
        print(content)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())