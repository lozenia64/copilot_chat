from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from services.copilot_auth import CopilotAuthError, CopilotAuthService
from services.copilot_chat import CopilotChatRequestError, CopilotChatService


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
    searchMode: str | None = None


@app.exception_handler(CopilotAuthError)
async def handle_copilot_auth_error(_: Request, exc: CopilotAuthError) -> JSONResponse:
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


@app.post("/api/copilot/status")
async def get_copilot_status(
    request: Request,
    response: Response,
    payload: CopilotEnvelopeRequest,
) -> dict[str, Any]:
    session_secret, created = auth_service.get_or_create_session_secret(request)
    if created:
        auth_service.apply_session_cookie(response, session_secret)
    return await auth_service.get_status_payload(payload.credentialEnvelope, session_secret)


@app.post("/api/copilot/login/start")
async def start_copilot_login(request: Request, response: Response) -> dict[str, Any]:
    session_secret, created = auth_service.get_or_create_session_secret(request)
    if created:
        auth_service.apply_session_cookie(response, session_secret)
    return await auth_service.start_login(session_secret)


@app.post("/api/copilot/login/poll")
async def poll_copilot_login(
    request: Request,
    response: Response,
    payload: CopilotLoginPollRequest,
) -> dict[str, Any]:
    session_secret, created = auth_service.get_or_create_session_secret(request)
    if created:
        auth_service.apply_session_cookie(response, session_secret)
    return await auth_service.poll_login(payload.loginId, session_secret)


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

    try:
        model, messages = chat_service.validate_chat_request(payload.model, payload.messages)
        search_mode = chat_service.normalize_search_mode(payload.searchMode)
    except CopilotChatRequestError as exc:
        return JSONResponse(
            status_code=400,
            content={"message": exc.message, "code": exc.code},
        )

    session_secret, created = auth_service.get_or_create_session_secret(request)
    copilot_session, refreshed_envelope = await auth_service.resolve_session(
        payload.credentialEnvelope,
        session_secret,
    )
    prepared_messages = await chat_service.prepare_messages_for_completion(
        messages,
        search_mode=search_mode,
    )

    stream_response = StreamingResponse(
        chat_service.stream_chat_completion(
            request=request,
            model=model,
            messages=prepared_messages,
            session=copilot_session,
            initiator_messages=messages,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    if created:
        auth_service.apply_session_cookie(stream_response, session_secret)
    if refreshed_envelope:
        stream_response.headers["X-Copilot-Credential-Envelope"] = refreshed_envelope
    return stream_response


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)