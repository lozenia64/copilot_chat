from __future__ import annotations

import json
import os

import litellm

from services.copilot_headers import build_copilot_headers


MODEL = os.getenv("COPILOT_TEST_MODEL", "gpt-5.4")
COPILOT_API_TOKEN = os.getenv("GITHUB_COPILOT_API_TOKEN")
COPILOT_API_BASE = os.getenv("GITHUB_COPILOT_API_BASE", "https://api.githubcopilot.com")


def main() -> None:
    if not COPILOT_API_TOKEN:
        raise SystemExit(
            "Set GITHUB_COPILOT_API_TOKEN to a user-specific Copilot API token before running this script."
        )

    headers = build_copilot_headers()
    headers["X-Initiator"] = "user"

    response = litellm.completion(
        model=MODEL,
        messages=[{"role": "user", "content": "hello"}],
        api_key=COPILOT_API_TOKEN,
        base_url=COPILOT_API_BASE,
        extra_headers=headers,
        custom_llm_provider="openai",
    )

    print(json.dumps(response.model_dump(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()