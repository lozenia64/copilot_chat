from __future__ import annotations

"""
Hardcoded-envelope live chat smoke test.

Purpose:
- Run a chat request without browser integration.
- Reuse a browser-copied credential envelope from localStorage.
- Reuse the matching session binding cookie from browser cookies.

How it works:
- Calls POST /api/copilot/status first (optional sanity check).
- Calls POST /api/chat with model gpt-5.4 and streams SSE output.

Important:
- The envelope and cookie must come from the SAME browser session.
- If either value is wrong/stale, server returns auth/binding errors.
"""

import json
import os
import sys
from typing import Any

import httpx
from dotenv import load_dotenv

# Load .env file from project root
load_dotenv()

# ===== Load from .env =====
CREDENTIAL_ENVELOPE = os.getenv("COPILOT_CREDENTIAL_ENVELOPE", "").strip()
SESSION_COOKIE_VALUE = os.getenv("COPILOT_SESSION_COOKIE_VALUE", "").strip()

# ===== Optional runtime config =====
BASE_URL = os.getenv("COPILOT_TEST_BASE_URL", "http://127.0.0.1:8000")
MODEL = os.getenv("COPILOT_TEST_MODEL", "gpt-5.4")
COOKIE_NAME = os.getenv("COPILOT_TEST_COOKIE_NAME", "copilot_client_binding")
PROMPT = os.getenv("COPILOT_TEST_PROMPT", "안녕! 지금 시각 기준으로 짧게 자기소개해줘.")
TIMEOUT_SECONDS = float(os.getenv("COPILOT_TEST_TIMEOUT", "60"))


def _extract_text_from_payload(payload: dict[str, Any]) -> str:
    choice = (payload.get("choices") or [{}])[0]
    delta = choice.get("delta") or choice.get("message") or {}
    content = delta.get("content")

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)

    return ""


def _require_non_placeholder(value: str, label: str, placeholder_prefix: str) -> None:
    if not value or value.startswith(placeholder_prefix):
        raise RuntimeError(
            f"{label} is not set. Replace the hardcoded placeholder first."
        )


def check_status(client: httpx.Client, envelope: str) -> None:
    response = client.post(
        "/api/copilot/status",
        json={"credentialEnvelope": envelope},
    )

    print(f"[status] HTTP {response.status_code}")
    try:
        payload = response.json()
    except json.JSONDecodeError:
        print("[status] non-JSON response:")
        print(response.text)
        response.raise_for_status()
        return

    print("[status] payload:")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    refreshed = response.headers.get("X-Copilot-Credential-Envelope")
    if refreshed:
        print("[status] received refreshed envelope header (can replace hardcoded value)")


def stream_chat(client: httpx.Client, envelope: str, model: str, prompt: str) -> int:
    request_body = {
        "model": model,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "credentialEnvelope": envelope,
    }

    print(f"[chat] model={model}")
    print(f"[chat] prompt={prompt}")
    print("[chat] streaming response:\n")

    with client.stream("POST", "/api/chat", json=request_body) as response:
        print(f"[chat] HTTP {response.status_code}")
        if response.status_code >= 400:
            text = response.text
            print("[chat] error response:")
            print(text)
            return 1

        saw_any_text = False
        for raw_line in response.iter_lines():
            if not raw_line:
                continue

            line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
            if not line.startswith("data:"):
                continue

            data = line[5:].strip()
            if data == "[DONE]":
                break

            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                continue

            if isinstance(payload, dict) and "code" in payload and "message" in payload:
                print(f"\n[chat] stream error: {payload.get('code')} - {payload.get('message')}")
                return 1

            chunk = _extract_text_from_payload(payload)
            if chunk:
                saw_any_text = True
                print(chunk, end="", flush=True)

        print()

    if not saw_any_text:
        print("[chat] warning: stream ended without visible text chunks")
    return 0


def main() -> int:
    envelope = CREDENTIAL_ENVELOPE
    session_cookie_value = SESSION_COOKIE_VALUE

    _require_non_placeholder(
        envelope,
        "COPILOT_CREDENTIAL_ENVELOPE (.env)",
        "COPILOT_CREDENTIAL_ENVELOPE",
    )
    _require_non_placeholder(
        session_cookie_value,
        "COPILOT_SESSION_COOKIE_VALUE (.env)",
        "COPILOT_SESSION_COOKIE_VALUE",
    )

    with httpx.Client(
        base_url=BASE_URL,
        timeout=TIMEOUT_SECONDS,
        headers={"Accept": "text/event-stream"},
        cookies={COOKIE_NAME: session_cookie_value},
    ) as client:
        check_status(client, envelope)
        return stream_chat(client, envelope, MODEL, PROMPT)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"[config] {exc}", file=sys.stderr)
        raise SystemExit(2)
