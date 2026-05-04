from __future__ import annotations

import uuid


def build_copilot_headers() -> dict[str, str]:
    return {
        "content-type": "application/json",
        "copilot-integration-id": "vscode-chat",
        "editor-plugin-version": "copilot-chat/0.26.7",
        "editor-version": "vscode/1.95.0",
        "openai-intent": "conversation-panel",
        "user-agent": "GitHubCopilotChat/0.26.7",
        "x-github-api-version": "2025-04-01",
        "x-request-id": str(uuid.uuid4()),
        "x-vscode-user-agent-library-version": "electron-fetch",
    }