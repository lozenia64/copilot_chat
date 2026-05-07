from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
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
    def __init__(
        self,
        config_path: Path,
        default_model: str,
    ) -> None:
        self.config_path = config_path
        self.default_model = default_model
        self.model_ids, self.litellm_model_by_id = self._load_model_config()
        self.allowed_model_ids = set(self.model_ids)
        self.stream_timeout = httpx.Timeout(connect=10.0, read=None, write=30.0, pool=30.0)

    def get_models_payload(self) -> dict[str, list[dict[str, str]]]:
        return {"data": [{"id": model_id} for model_id in self.model_ids]}

    def resolve_model(self, model: str | None) -> str:
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
        return model_id

    def resolve_litellm_model(self, model: str) -> str:
        return self.litellm_model_by_id.get(model, model)

    def validate_chat_request(
        self,
        model: str | None,
        messages: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]]]:
        model_id = self.resolve_model(model)
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
        initiator_messages: list[dict[str, Any]] | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: Any = None,
        parallel_tool_calls: bool | None = None,
    ) -> AsyncIterator[bytes]:
        extra_headers = self._build_extra_headers(initiator_messages or messages)
        stream = None
        provider_model = self.resolve_litellm_model(model)

        kwargs: dict[str, Any] = {
            "model": provider_model,
            "messages": messages,
            "stream": True,
            "api_key": session.copilot_api_token,
            "base_url": session.copilot_api_base,
            "extra_headers": extra_headers,
            "custom_llm_provider": "openai",
            "timeout": self.stream_timeout,
        }
        if tools is not None:
            kwargs["tools"] = tools
        if tool_choice is not None:
            kwargs["tool_choice"] = tool_choice
        if parallel_tool_calls is not None:
            kwargs["parallel_tool_calls"] = parallel_tool_calls

        try:
            stream = await litellm.acompletion(**kwargs)

            async for chunk in stream:
                if await request.is_disconnected():
                    break

                payload, should_stop = self._sanitize_stream_chunk(chunk)
                if payload is None:
                    continue

                yield self._format_sse(payload)
                if should_stop:
                    break
        except Exception as e:
            self._log_stream_failure(e)
            yield self._format_sse(self._stream_error_payload(e))
        finally:
            if stream is not None:
                close_method = getattr(stream, "aclose", None)
                if callable(close_method):
                    await close_method()
            yield b"data: [DONE]\n\n"

    def _log_stream_failure(self, exc: Exception) -> None:
        error_code = self._stream_error_payload(exc)["code"]
        LOGGER.warning(
            "Copilot chat streaming failed; returning sanitized SSE error (%s)",
            error_code,
            exc_info=exc,
        )

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

    def _coerce_message_content_to_text(self, content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
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

        return "".join(parts).strip()

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

        result_delta: dict[str, Any] = {}

        content = self._coerce_stream_content_to_text(delta.get("content"))
        if content:
            result_delta["content"] = content

        reasoning_content = delta.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content:
            result_delta["reasoning_content"] = reasoning_content

        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            result_delta["tool_calls"] = tool_calls

        if not result_delta:
            return None

        return {"choices": [{"delta": result_delta}]}

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

    def _stream_error_payload(self, exc: Exception | None = None) -> dict[str, Any]:
        if exc is not None:
            exc_str = str(exc).lower()
            if "model_not_supported" in exc_str or "requested model is not supported" in exc_str:
                return {
                    "code": "copilot_model_not_supported",
                    "message": "현재 로그인 되어있는 GitHub 계정에서는 해당 모델을 사용할 수 없습니다.",
                }
            if "ratelimiterror" in type(exc).__name__.lower() or "rate limit" in exc_str or "429" in exc_str:
                return {
                    "code": "copilot_rate_limit_exceeded",
                    "message": "주간 사용량 한도를 초과하여 채팅 응답을 생성하지 못했습니다. 잠시 후 다시 시도하세요.",
                }
        return {
            "code": "copilot_chat_stream_failed",
            "message": "채팅 응답을 생성하지 못했습니다. 잠시 후 다시 시도하세요.",
        }

    def _load_model_config(self) -> tuple[list[str], dict[str, str]]:
        if not self.config_path.exists():
            return [self.default_model], {self.default_model: self.default_model}

        try:
            config = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            LOGGER.warning("Failed to read model config %s: %s", self.config_path, exc)
            return [self.default_model], {self.default_model: self.default_model}

        raw_models = config.get("model_list")
        if not isinstance(raw_models, list):
            return [self.default_model], {self.default_model: self.default_model}

        seen: set[str] = set()
        model_ids: list[str] = []
        litellm_model_by_id: dict[str, str] = {}
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            model_name = item.get("model_name")
            if not isinstance(model_name, str):
                continue
            model_id = model_name.strip()
            if not model_id or model_id in seen:
                continue
            provider_model = model_id
            litellm_params = item.get("litellm_params")
            if isinstance(litellm_params, dict):
                configured_model = litellm_params.get("model")
                if isinstance(configured_model, str) and configured_model.strip():
                    provider_model = configured_model.strip()
            seen.add(model_id)
            model_ids.append(model_id)
            litellm_model_by_id[model_id] = provider_model

        if not model_ids:
            return [self.default_model], {self.default_model: self.default_model}

        if self.default_model not in seen:
            model_ids.insert(0, self.default_model)
            litellm_model_by_id[self.default_model] = self.default_model
        return model_ids, litellm_model_by_id