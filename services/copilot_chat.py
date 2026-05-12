from __future__ import annotations

import base64
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
from .web_search import (
    WEB_SEARCH_TOOL_NAME,
    WebSearchClient,
    format_tool_result_content,
)

LOGGER = logging.getLogger(__name__)
class CopilotChatRequestError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CopilotChatService:
    # web_search 등 도구 호출 응답을 받고 다시 모델을 호출하는 사이클의 최대 반복 횟수.
    # 무한 루프 방지용. 일반적으로 1~2회면 충분하다.
    MAX_TOOL_CALL_ITERATIONS = 3

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
        self.search_client = WebSearchClient()

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

    def ensure_model_supports_vision(self, model: str) -> None:
        provider_model = self.resolve_litellm_model(model)
        try:
            supports_vision = bool(litellm.supports_vision(model=provider_model))
        except Exception:
            supports_vision = False
        if not supports_vision:
            raise CopilotChatRequestError(
                code="chat_model_not_vision_capable",
                message="선택한 모델은 이미지 첨부를 지원하지 않습니다. 다른 모델을 선택하세요.",
            )

    def build_provider_messages(
        self,
        prior_messages: list[dict[str, Any]],
        new_user_message: dict[str, Any],
    ) -> list[dict[str, Any]]:
        provider_messages: list[dict[str, Any]] = []

        for message in prior_messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if not isinstance(role, str) or not role.strip():
                continue
            content = self._build_history_message_content(message)
            if not content:
                continue
            provider_messages.append({"role": role.strip(), "content": content})

        provider_messages.append(
            {
                "role": "user",
                "content": self._build_current_user_message_content(new_user_message),
            }
        )
        return provider_messages

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

    def _build_history_message_content(self, message: dict[str, Any]) -> str:
        content_text = self._coerce_message_content_to_text(message.get("content"))
        attachments = message.get("attachments") if isinstance(message.get("attachments"), list) else []
        attachment_summary = self._build_attachment_summary_text(attachments)
        if content_text and attachment_summary:
            return f"{content_text}\n\n{attachment_summary}"
        return content_text or attachment_summary

    def _build_current_user_message_content(self, message: dict[str, Any]) -> str | list[dict[str, Any]]:
        content_text = self._coerce_message_content_to_text(message.get("content"))
        attachments = message.get("attachments") if isinstance(message.get("attachments"), list) else []
        if not attachments:
            return content_text

        parts: list[dict[str, Any]] = []
        if content_text:
            parts.append({"type": "text", "text": content_text})
        for attachment in attachments:
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": self._attachment_to_data_url(attachment)},
                }
            )
        return parts

    def _attachment_to_data_url(self, attachment: dict[str, Any]) -> str:
        storage_path = attachment.get("storagePath")
        if not isinstance(storage_path, str) or not storage_path.strip():
            raise CopilotChatRequestError(
                code="attachment_not_found",
                message="첨부 이미지를 찾을 수 없습니다. 다시 업로드하세요.",
            )
        path = Path(storage_path)
        if not path.exists() or not path.is_file():
            raise CopilotChatRequestError(
                code="attachment_not_found",
                message="첨부 이미지를 찾을 수 없습니다. 다시 업로드하세요.",
            )
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"

    def _build_attachment_summary_text(self, attachments: list[dict[str, Any]]) -> str:
        if not attachments:
            return ""
        file_names = [
            str(attachment.get("fileName")).strip()
            for attachment in attachments
            if isinstance(attachment, dict) and isinstance(attachment.get("fileName"), str) and attachment.get("fileName").strip()
        ]
        if not file_names:
            return f"[사용자가 이미지 {len(attachments)}개를 첨부함]"
        return f"[사용자가 이미지 {len(attachments)}개를 첨부함: {', '.join(file_names)}]"



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
        """LiteLLM 채팅 완성 스트림을 도구 호출 루프로 감싼다.

        - 모델 응답에 tool_calls 가 있으면 서버 측에서 실행 후 다음 라운드에 첨부.
        - 브라우저로는 텍스트 콘텐츠와 도구 진행 표시(`🔎 ...`)만 흘려보낸다.
          tool_calls 델타 자체는 외부 SSE 로 노출되지 않는다.
        - 라운드는 MAX_TOOL_CALL_ITERATIONS 회로 제한.
        """
        extra_headers = self._build_extra_headers(initiator_messages or messages)
        provider_model = self.resolve_litellm_model(model)
        working_messages: list[dict[str, Any]] = list(messages)

        try:
            for _iteration in range(self.MAX_TOOL_CALL_ITERATIONS):
                kwargs: dict[str, Any] = {
                    "model": provider_model,
                    "messages": working_messages,
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

                stream = None
                tool_call_accumulator: dict[int, dict[str, Any]] = {}
                emitted_error = False
                client_disconnected = False

                try:
                    stream = await litellm.acompletion(**kwargs)

                    async for chunk in stream:
                        if await request.is_disconnected():
                            client_disconnected = True
                            break

                        raw_payload = self._normalize_stream_chunk(chunk)
                        if self._is_error_like_stream_payload(raw_payload):
                            yield self._format_sse(self._stream_error_payload())
                            emitted_error = True
                            break

                        self._accumulate_tool_call_deltas(raw_payload, tool_call_accumulator)

                        visible_payload = self._extract_visible_text_payload(raw_payload)
                        if visible_payload is not None:
                            yield self._format_sse(visible_payload)
                finally:
                    if stream is not None:
                        close_method = getattr(stream, "aclose", None)
                        if callable(close_method):
                            await close_method()

                if emitted_error or client_disconnected:
                    return

                if not tool_call_accumulator:
                    return

                finalized_tool_calls = self._finalize_tool_calls(tool_call_accumulator)
                if not finalized_tool_calls:
                    return

                working_messages = working_messages + [
                    {"role": "assistant", "content": None, "tool_calls": finalized_tool_calls}
                ]

                for tool_call in finalized_tool_calls:
                    status_payload = self._tool_status_payload(tool_call)
                    if status_payload is not None:
                        yield self._format_sse(status_payload)
                    tool_message = await self._execute_tool_call(tool_call)
                    working_messages.append(tool_message)

            LOGGER.warning(
                "Tool-call loop reached max iterations (%d) without final answer",
                self.MAX_TOOL_CALL_ITERATIONS,
            )
            yield self._format_sse(self._stream_error_payload())
        except Exception as exc:
            self._log_stream_failure(exc)
            yield self._format_sse(self._stream_error_payload(exc))
        finally:
            yield b"data: [DONE]\n\n"

    # ------------------------------------------------------------------
    # 도구 호출 누적/실행 헬퍼
    # ------------------------------------------------------------------

    def _accumulate_tool_call_deltas(
        self,
        payload: dict[str, Any],
        accumulator: dict[int, dict[str, Any]],
    ) -> None:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return
        choice = choices[0]
        if not isinstance(choice, dict):
            return
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else choice.get("message")
        if not isinstance(delta, dict):
            return
        tool_calls = delta.get("tool_calls")
        if not isinstance(tool_calls, list):
            return

        for entry in tool_calls:
            if not isinstance(entry, dict):
                continue
            index = entry.get("index")
            if not isinstance(index, int):
                continue
            slot = accumulator.setdefault(
                index,
                {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
            )
            if isinstance(entry.get("id"), str) and entry["id"]:
                slot["id"] = entry["id"]
            if isinstance(entry.get("type"), str) and entry["type"]:
                slot["type"] = entry["type"]
            function_part = entry.get("function")
            if isinstance(function_part, dict):
                name_part = function_part.get("name")
                # OpenAI 스트림에서 function.name 은 보통 첫 청크에서만 옴.
                # 이미 채워진 경우 덮어쓰지 않는다.
                if isinstance(name_part, str) and name_part and not slot["function"].get("name"):
                    slot["function"]["name"] = name_part
                arguments_part = function_part.get("arguments")
                if isinstance(arguments_part, str):
                    slot["function"]["arguments"] = (slot["function"].get("arguments") or "") + arguments_part

    def _finalize_tool_calls(self, accumulator: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
        finalized: list[dict[str, Any]] = []
        for index in sorted(accumulator.keys()):
            slot = accumulator[index]
            function_part = slot.get("function") or {}
            name = function_part.get("name") or ""
            if not name:
                continue
            finalized.append({
                "id": slot.get("id") or f"call_{index}",
                "type": slot.get("type") or "function",
                "function": {
                    "name": name,
                    "arguments": function_part.get("arguments") or "",
                },
            })
        return finalized

    async def _execute_tool_call(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        function_part = tool_call.get("function") or {}
        name = function_part.get("name") or ""
        arguments_raw = function_part.get("arguments") or ""
        call_id = tool_call.get("id") or ""

        if name != WEB_SEARCH_TOOL_NAME:
            return self._tool_result_message(
                call_id,
                name,
                {"error": f"Unsupported tool: {name}"},
            )

        query = self._extract_search_query(arguments_raw)
        if not query:
            return self._tool_result_message(
                call_id,
                name,
                {"error": "Missing or invalid 'query' argument."},
            )

        try:
            results = await self.search_client.search(query)
        except Exception as exc:
            LOGGER.warning(
                "Tool call '%s' raised %s; returning sanitized error to model",
                name,
                type(exc).__name__,
                exc_info=exc,
            )
            return self._tool_result_message(
                call_id,
                name,
                {"query": query, "error": "Web search provider unavailable."},
            )

        return {
            "role": "tool",
            "tool_call_id": call_id,
            "name": name,
            "content": format_tool_result_content(query, results),
        }

    def _tool_result_message(
        self,
        call_id: str,
        name: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "name": name,
            "content": json.dumps(payload, ensure_ascii=False),
        }

    def _extract_search_query(self, arguments_raw: str) -> str:
        if not arguments_raw:
            return ""
        try:
            parsed = json.loads(arguments_raw)
        except json.JSONDecodeError:
            return ""
        if not isinstance(parsed, dict):
            return ""
        query = parsed.get("query")
        if isinstance(query, str):
            stripped = query.strip()
            if stripped:
                return stripped
        return ""

    def _tool_status_payload(self, tool_call: dict[str, Any]) -> dict[str, Any] | None:
        function_part = tool_call.get("function") or {}
        if function_part.get("name") != WEB_SEARCH_TOOL_NAME:
            return None
        query = self._extract_search_query(function_part.get("arguments") or "")
        if not query:
            return None
        return {
            "choices": [
                {"delta": {"content": f"\n_🔎 웹 검색 중: {query}_\n\n"}}
            ]
        }

    def _extract_visible_text_payload(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """LiteLLM 청크에서 사용자에게 노출할 텍스트 부분만 추출.

        tool_calls 델타는 의도적으로 제외한다 (내부 누적기에서 따로 처리).
        """
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

        if not result_delta:
            return None

        return {"choices": [{"delta": result_delta}]}

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