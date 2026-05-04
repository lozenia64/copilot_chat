from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
import litellm
import yaml
from fastapi import Request

from .copilot_auth import CopilotCredentialSession
from .copilot_headers import build_copilot_headers


LOGGER = logging.getLogger(__name__)


class CopilotChatRequestError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CopilotChatService:
    def __init__(self, config_path: Path, default_model: str) -> None:
        self.config_path = config_path
        self.default_model = default_model
        self.model_ids = self._load_model_ids()
        self.allowed_model_ids = set(self.model_ids)
        self.stream_timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=30.0)

    def get_models_payload(self) -> dict[str, list[dict[str, str]]]:
        return {"data": [{"id": model_id} for model_id in self.model_ids]}

    def validate_chat_request(
        self,
        model: str | None,
        messages: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]]]:
        model_id = (model or self.default_model).strip()
        if not model_id:
            raise CopilotChatRequestError(
                code="chat_model_required",
                message="채팅 모델을 확인할 수 없습니다. 다시 시도하세요.",
            )
        if model_id not in self.allowed_model_ids:
            raise CopilotChatRequestError(
                code="chat_model_not_allowed",
                message="선택한 모델은 사용할 수 없습니다. 목록에서 다시 선택하세요.",
            )
        if not isinstance(messages, list) or not messages:
            raise CopilotChatRequestError(
                code="chat_messages_invalid",
                message="채팅 메시지 형식이 올바르지 않습니다. 다시 시도하세요.",
            )

        normalized_messages: list[dict[str, Any]] = []
        for message in messages:
            if not isinstance(message, dict):
                raise CopilotChatRequestError(
                    code="chat_messages_invalid",
                    message="채팅 메시지 형식이 올바르지 않습니다. 다시 시도하세요.",
                )

            role = message.get("role")
            content = message.get("content")
            if not isinstance(role, str) or not role.strip():
                raise CopilotChatRequestError(
                    code="chat_messages_invalid",
                    message="채팅 메시지 형식이 올바르지 않습니다. 다시 시도하세요.",
                )
            if not isinstance(content, str) and not isinstance(content, list):
                raise CopilotChatRequestError(
                    code="chat_messages_invalid",
                    message="채팅 메시지 형식이 올바르지 않습니다. 다시 시도하세요.",
                )

            normalized_message = dict(message)
            normalized_message["role"] = role.strip()
            normalized_message["content"] = content
            normalized_messages.append(normalized_message)

        return model_id, normalized_messages

    async def stream_chat_completion(
        self,
        request: Request,
        model: str,
        messages: list[dict[str, Any]],
        session: CopilotCredentialSession,
    ) -> AsyncIterator[bytes]:
        extra_headers = self._build_extra_headers(messages)
        stream = None

        try:
            stream = await litellm.acompletion(
                model=model,
                messages=messages,
                stream=True,
                api_key=session.copilot_api_token,
                base_url=session.copilot_api_base,
                extra_headers=extra_headers,
                custom_llm_provider="openai",
                timeout=self.stream_timeout,
            )

            async for chunk in stream:
                if await request.is_disconnected():
                    break

                payload, should_stop = self._sanitize_stream_chunk(chunk)
                if payload is None:
                    continue

                yield self._format_sse(payload)
                if should_stop:
                    break
        except Exception:
            LOGGER.exception("Copilot chat streaming failed")
            yield self._format_sse(self._stream_error_payload())
        finally:
            if stream is not None:
                close_method = getattr(stream, "aclose", None)
                if callable(close_method):
                    await close_method()
            yield b"data: [DONE]\n\n"

    def _build_extra_headers(
        self,
        messages: list[dict[str, Any]],
    ) -> dict[str, str]:
        headers = build_copilot_headers()
        headers["X-Initiator"] = self._determine_initiator(messages)
        if self._has_vision_content(messages):
            headers["Copilot-Vision-Request"] = "true"
        return headers

    def _determine_initiator(self, messages: list[dict[str, Any]]) -> str:
        for message in messages:
            role = message.get("role")
            if role in {"assistant", "tool"}:
                return "agent"
        return "user"

    def _has_vision_content(self, messages: list[dict[str, Any]]) -> bool:
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "image_url" or "image_url" in item:
                    return True
        return False

    def _normalize_stream_chunk(self, chunk: Any) -> dict[str, Any]:
        if hasattr(chunk, "model_dump"):
            return chunk.model_dump(exclude_none=True)
        if hasattr(chunk, "dict"):
            return chunk.dict(exclude_none=True)
        if isinstance(chunk, dict):
            return chunk
        if isinstance(chunk, str):
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                return {"choices": [{"delta": {"content": chunk}}]}
        return {"choices": [{"delta": {"content": str(chunk)}}]}

    def _sanitize_stream_chunk(self, chunk: Any) -> tuple[dict[str, Any] | None, bool]:
        payload = self._normalize_stream_chunk(chunk)
        if self._is_error_like_stream_payload(payload):
            return self._stream_error_payload(), True

        safe_payload = self._extract_text_stream_payload(payload)
        if safe_payload is None:
            return None, False
        return safe_payload, False

    def _is_error_like_stream_payload(self, payload: dict[str, Any]) -> bool:
        if "error" in payload or "detail" in payload:
            return True
        if isinstance(payload.get("message"), str) and not payload.get("choices"):
            return True

        choices = payload.get("choices")
        if not isinstance(choices, list):
            return False

        for choice in choices:
            if not isinstance(choice, dict):
                continue
            if "error" in choice or "detail" in choice:
                return True

        return False

    def _extract_text_stream_payload(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return None

        choice = choices[0]
        if not isinstance(choice, dict):
            return None

        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = choice.get("message")
        if not isinstance(delta, dict):
            return None

        content = self._coerce_stream_content_to_text(delta.get("content"))
        if content:
            return {"choices": [{"delta": {"content": content}}]}

        reasoning_content = delta.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content:
            return {"choices": [{"delta": {"reasoning_content": reasoning_content}}]}

        return None

    def _coerce_stream_content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""

        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue

            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
                continue

            nested_content = item.get("content")
            if isinstance(nested_content, str):
                parts.append(nested_content)

        return "".join(parts)

    def _format_sse(self, payload: dict[str, Any]) -> bytes:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")

    def _stream_error_payload(self) -> dict[str, Any]:
        return {
            "code": "copilot_chat_stream_failed",
            "message": "채팅 응답을 생성하지 못했습니다. 잠시 후 다시 시도하세요.",
        }

    def _load_model_ids(self) -> list[str]:
        if not self.config_path.exists():
            return [self.default_model]

        try:
            config = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            LOGGER.warning("Failed to read model config %s: %s", self.config_path, exc)
            return [self.default_model]

        raw_models = config.get("model_list")
        if not isinstance(raw_models, list):
            return [self.default_model]

        seen: set[str] = set()
        model_ids: list[str] = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            model_name = item.get("model_name")
            if not isinstance(model_name, str):
                continue
            model_id = model_name.strip()
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            model_ids.append(model_id)

        if not model_ids:
            return [self.default_model]

        if self.default_model not in seen:
            model_ids.insert(0, self.default_model)
        return model_ids