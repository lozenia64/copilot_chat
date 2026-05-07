from __future__ import annotations

import logging
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from services.copilot_auth import CopilotAuthError, CopilotAuthService
from services.copilot_chat import CopilotChatRequestError, CopilotChatService
from services.conversation_service import ConversationService, ConversationStateError


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_PATH = BASE_DIR / "litellm_config.yaml"
INDEX_PATH = STATIC_DIR / "index.html"

load_dotenv(BASE_DIR / ".env")

DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gpt-5.4")

app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

auth_service = CopilotAuthService()
chat_service = CopilotChatService(config_path=CONFIG_PATH, default_model=DEFAULT_MODEL)
conversation_service = ConversationService()
LOGGER = logging.getLogger("uvicorn.error")


@dataclass(slots=True)
class BrowserSessionContext:
    session_secret: str
    conversation_scope_id: str
    created_session_cookie: bool


def _build_index_html() -> str:
    style_version = int((STATIC_DIR / "style.css").stat().st_mtime)
    app_version = int((STATIC_DIR / "app.js").stat().st_mtime)
    index_html = INDEX_PATH.read_text(encoding="utf-8")
    index_html = index_html.replace(
        "/static/style.css",
        f"/static/style.css?v={style_version}",
        1,
    )
    index_html = index_html.replace(
        "/static/app.js",
        f"/static/app.js?v={app_version}",
        1,
    )
    return index_html


class CopilotEnvelopeRequest(BaseModel):
    credentialEnvelope: str | None = None


class CopilotLoginPollRequest(BaseModel):
    loginId: str


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[dict[str, Any]]
    credentialEnvelope: str | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    parallel_tool_calls: bool | None = None


class ConversationCreateRequest(BaseModel):
    model: str | None = None
    credentialEnvelope: str | None = None


class ConversationStateRequest(BaseModel):
    credentialEnvelope: str | None = None


class ConversationActivateRequest(BaseModel):
    credentialEnvelope: str | None = None


class ConversationModelRequest(BaseModel):
    model: str | None = None
    credentialEnvelope: str | None = None


class ConversationMessageRequest(BaseModel):
    content: str
    model: str | None = None
    credentialEnvelope: str | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    parallel_tool_calls: bool | None = None


def _resolve_browser_session_context(request: Request) -> BrowserSessionContext:
    session_secret, created = auth_service.get_or_create_session_secret(request)
    request.state.browser_session_cookie_created = created
    return BrowserSessionContext(
        session_secret=session_secret,
        conversation_scope_id=auth_service.get_conversation_scope(session_secret),
        created_session_cookie=created,
    )


def _format_log_context(log_context: dict[str, Any]) -> str:
    return ", ".join(f"{key}={value}" for key, value in log_context.items() if value is not None)


def _apply_browser_session_cookie(response: Response, browser_session: BrowserSessionContext) -> None:
    if browser_session.created_session_cookie:
        auth_service.apply_session_cookie(response, browser_session.session_secret)


def _apply_credential_envelope_header(response: Response, credential_envelope: str | None) -> None:
    if credential_envelope:
        response.headers["X-Copilot-Credential-Envelope"] = credential_envelope


def _build_streaming_response(stream) -> StreamingResponse:
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.exception_handler(CopilotAuthError)
async def handle_copilot_auth_error(request: Request, exc: CopilotAuthError) -> JSONResponse:
    if exc.code in {"copilot_login_session_mismatch", "copilot_upstream_error"}:
        log_context = {
            "method": request.method,
            "path": request.url.path,
            "status_code": exc.status_code,
            "code": exc.code,
            "host": request.headers.get("host"),
            "origin": request.headers.get("origin"),
            "referer": request.headers.get("referer"),
            "client_host": request.client.host if request.client is not None else None,
            "browser_session_cookie_created": getattr(
                request.state,
                "browser_session_cookie_created",
                None,
            ),
        }
        log_context.update(exc.log_context)
        LOGGER.warning("Copilot auth error: %s", _format_log_context(log_context))
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": exc.message, "code": exc.code},
    )


@app.exception_handler(CopilotChatRequestError)
async def handle_copilot_chat_request_error(_: Request, exc: CopilotChatRequestError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"message": exc.message, "code": exc.code},
    )


@app.exception_handler(ConversationStateError)
async def handle_conversation_state_error(_: Request, exc: ConversationStateError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"message": exc.message, "code": exc.code},
    )


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "message": "요청 형식이 올바르지 않습니다. 다시 시도하세요.",
            "code": "request_validation_failed",
        },
    )


@app.get("/")
async def index() -> HTMLResponse:
    return HTMLResponse(
        content=_build_index_html(),
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/api/models")
async def get_models() -> dict[str, list[dict[str, str]]]:
    return chat_service.get_models_payload()


@app.get("/api/conversations")
async def get_conversations(request: Request, response: Response) -> dict[str, Any]:
    browser_session = _resolve_browser_session_context(request)
    _apply_browser_session_cookie(response, browser_session)
    return conversation_service.get_state_payload(
        auth_service.get_anonymous_history_scope(browser_session.conversation_scope_id)
    )


@app.post("/api/conversations/state")
async def get_conversations_state(
    request: Request,
    response: Response,
    payload: ConversationStateRequest,
) -> dict[str, Any]:
    browser_session = _resolve_browser_session_context(request)
    _apply_browser_session_cookie(response, browser_session)
    owner_context, refreshed_envelope = await auth_service.resolve_history_scope(
        payload.credentialEnvelope,
        browser_session.session_secret,
        browser_session.conversation_scope_id,
    )
    _apply_credential_envelope_header(response, refreshed_envelope)
    return conversation_service.get_state_payload(owner_context.scope_id)


@app.post("/api/conversations")
async def create_conversation(
    request: Request,
    response: Response,
    payload: ConversationCreateRequest,
) -> dict[str, Any]:
    browser_session = _resolve_browser_session_context(request)
    model_id = chat_service.resolve_model(payload.model)
    owner_context, refreshed_envelope = await auth_service.resolve_history_scope(
        payload.credentialEnvelope,
        browser_session.session_secret,
        browser_session.conversation_scope_id,
    )
    session = conversation_service.create_conversation(owner_context.scope_id, model_id)
    _apply_browser_session_cookie(response, browser_session)
    _apply_credential_envelope_header(response, refreshed_envelope)
    return {
        "session": session,
        "activeSessionId": session["id"],
    }


@app.post("/api/conversations/{conversation_id}/activate")
async def activate_conversation(
    conversation_id: str,
    request: Request,
    response: Response,
    payload: ConversationActivateRequest | None = None,
) -> dict[str, Any]:
    browser_session = _resolve_browser_session_context(request)
    owner_context, refreshed_envelope = await auth_service.resolve_history_scope(
        None if payload is None else payload.credentialEnvelope,
        browser_session.session_secret,
        browser_session.conversation_scope_id,
    )
    conversation_service.activate_conversation(owner_context.scope_id, conversation_id)
    _apply_browser_session_cookie(response, browser_session)
    _apply_credential_envelope_header(response, refreshed_envelope)
    return {"activeSessionId": conversation_id}


@app.post("/api/conversations/{conversation_id}/model")
async def update_conversation_model(
    conversation_id: str,
    request: Request,
    response: Response,
    payload: ConversationModelRequest,
) -> dict[str, Any]:
    browser_session = _resolve_browser_session_context(request)
    model_id = chat_service.resolve_model(payload.model)
    owner_context, refreshed_envelope = await auth_service.resolve_history_scope(
        payload.credentialEnvelope,
        browser_session.session_secret,
        browser_session.conversation_scope_id,
    )
    session = conversation_service.update_conversation_model(
        owner_context.scope_id,
        conversation_id,
        model_id,
    )
    _apply_browser_session_cookie(response, browser_session)
    _apply_credential_envelope_header(response, refreshed_envelope)
    return {"session": session}


@app.post("/api/conversations/{conversation_id}/messages")
async def send_conversation_message(
    conversation_id: str,
    request: Request,
    payload: ConversationMessageRequest,
):
    if not payload.credentialEnvelope:
        raise CopilotAuthError(
            status_code=401,
            message="이 브라우저의 GitHub Copilot 자격 정보가 없습니다. 먼저 로그인하세요.",
            code="copilot_login_required",
        )

    content = conversation_service.validate_message_content(payload.content)
    model_id = chat_service.resolve_model(payload.model) if payload.model is not None else None

    browser_session = _resolve_browser_session_context(request)
    copilot_session, refreshed_envelope = await auth_service.resolve_session(
        payload.credentialEnvelope,
        browser_session.session_secret,
    )
    owner_context = auth_service.get_authenticated_history_owner(copilot_session)
    turn = conversation_service.begin_turn(
        owner_context.scope_id,
        conversation_id,
        content=content,
        model=model_id,
    )

    stream_response = _build_streaming_response(
        conversation_service.persist_stream(
            request=request,
            scope_id=owner_context.scope_id,
            conversation_id=conversation_id,
            assistant_message_id=turn.assistant_message_id,
            stream=chat_service.stream_chat_completion(
                request=request,
                model=turn.model,
                messages=turn.visible_messages,
                session=copilot_session,
                initiator_messages=turn.visible_messages,
                tools=payload.tools,
                tool_choice=payload.tool_choice,
                parallel_tool_calls=payload.parallel_tool_calls,
            ),
        )
    )
    _apply_browser_session_cookie(stream_response, browser_session)
    if refreshed_envelope:
        stream_response.headers["X-Copilot-Credential-Envelope"] = refreshed_envelope
    return stream_response


@app.post("/api/copilot/status")
async def get_copilot_status(
    request: Request,
    response: Response,
    payload: CopilotEnvelopeRequest,
) -> dict[str, Any]:
    browser_session = _resolve_browser_session_context(request)
    _apply_browser_session_cookie(response, browser_session)
    status_payload, refreshed_envelope = await auth_service.get_status_payload(
        payload.credentialEnvelope,
        browser_session.session_secret,
    )
    _apply_credential_envelope_header(response, refreshed_envelope)
    return status_payload


@app.post("/api/copilot/login/start")
async def start_copilot_login(request: Request, response: Response) -> dict[str, Any]:
    browser_session = _resolve_browser_session_context(request)
    _apply_browser_session_cookie(response, browser_session)
    return await auth_service.start_login(browser_session.session_secret)


@app.post("/api/copilot/login/poll")
async def poll_copilot_login(
    request: Request,
    response: Response,
    payload: CopilotLoginPollRequest,
) -> dict[str, Any]:
    browser_session = _resolve_browser_session_context(request)
    _apply_browser_session_cookie(response, browser_session)
    return await auth_service.poll_login(payload.loginId, browser_session.session_secret)


@app.post("/api/copilot/logout")
async def logout_copilot(response: Response) -> dict[str, bool]:
    auth_service.rotate_session_secret(response)
    return {"authenticated": False}


@app.post("/api/chat")
async def chat(request: Request, payload: ChatRequest):
    if not payload.credentialEnvelope:
        raise CopilotAuthError(
            status_code=401,
            message="이 브라우저의 GitHub Copilot 자격 정보가 없습니다. 먼저 로그인하세요.",
            code="copilot_login_required",
        )

    model, messages = chat_service.validate_chat_request(payload.model, payload.messages)

    browser_session = _resolve_browser_session_context(request)
    copilot_session, refreshed_envelope = await auth_service.resolve_session(
        payload.credentialEnvelope,
        browser_session.session_secret,
    )

    stream_response = _build_streaming_response(
        chat_service.stream_chat_completion(
            request=request,
            model=model,
            messages=messages,
            session=copilot_session,
            initiator_messages=messages,
            tools=payload.tools,
            tool_choice=payload.tool_choice,
            parallel_tool_calls=payload.parallel_tool_calls,
        )
    )
    _apply_browser_session_cookie(stream_response, browser_session)
    if refreshed_envelope:
        stream_response.headers["X-Copilot-Credential-Envelope"] = refreshed_envelope
    return stream_response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)