from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from services.copilot_auth import CopilotAuthError, CopilotAuthService
from services.copilot_chat import CopilotChatRequestError, CopilotChatService
from services.conversation_service import ConversationService, ConversationStateError
from services.image_attachment_service import ImageAttachmentError, ImageAttachmentService


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_PATH = BASE_DIR / "litellm_config.yaml"
INDEX_PATH = STATIC_DIR / "index.html"
DOWNLOAD_APP_PAGE_PATH = STATIC_DIR / "download_app.html"
DOWNLOAD_APP_CSS_PATH = STATIC_DIR / "download_app.css"
DOWNLOAD_APP_JS_PATH = STATIC_DIR / "download_app.js"
DOWNLOAD_APP_DIR = BASE_DIR / "downloads" / "app"

load_dotenv(BASE_DIR / ".env")

DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "gpt-5.4")

app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

auth_service = CopilotAuthService()
chat_service = CopilotChatService(config_path=CONFIG_PATH, default_model=DEFAULT_MODEL)
conversation_service = ConversationService()
image_attachment_service = ImageAttachmentService()
LOGGER = logging.getLogger("uvicorn.error")


@dataclass(slots=True)
class BrowserSessionContext:
    session_secret: str
    conversation_scope_id: str
    created_session_cookie: bool


@dataclass(slots=True)
class DownloadArtifactSpec:
    slug: str
    label: str
    description: str
    default_filename: str
    media_type: str


DOWNLOAD_ARTIFACT_SPECS: dict[str, DownloadArtifactSpec] = {
    "apk": DownloadArtifactSpec(
        slug="apk",
        label="Android APK",
        description="Android 기기에서 직접 설치할 수 있는 앱 파일입니다.",
        default_filename="pilotchat.apk",
        media_type="application/vnd.android.package-archive",
    ),
    "plist": DownloadArtifactSpec(
        slug="plist",
        label="iOS Manifest (plist)",
        description="직접 배포용 iOS manifest 파일입니다.",
        default_filename="pilotchat.plist",
        media_type="application/xml",
    ),
    "ipa": DownloadArtifactSpec(
        slug="ipa",
        label="iOS App (ipa)",
        description="직접 배포용 iPhone/iPad 앱 패키지입니다.",
        default_filename="pilotchat.ipa",
        media_type="application/octet-stream",
    ),
}


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


def _resolve_download_artifact_path(spec: DownloadArtifactSpec) -> Path:
    configured_path = os.getenv(f"DOWNLOAD_APP_{spec.slug.upper()}_PATH")
    if configured_path:
        return Path(configured_path).expanduser()
    return DOWNLOAD_APP_DIR / spec.default_filename


def _build_download_app_page_config(request: Request) -> dict[str, Any]:
    artifacts: dict[str, dict[str, Any]] = {}

    for slug, spec in DOWNLOAD_ARTIFACT_SPECS.items():
        artifact_path = _resolve_download_artifact_path(spec)
        available = artifact_path.exists() and artifact_path.is_file()
        artifacts[slug] = {
            "label": spec.label,
            "description": spec.description,
            "available": available,
            "filename": artifact_path.name if available else spec.default_filename,
            "downloadUrl": (
                str(request.url_for("download_app_artifact", artifact=slug))
                if available
                else None
            ),
        }

    plist_url = artifacts["plist"].get("downloadUrl")
    ios_install_url = (
        f"itms-services://?action=download-manifest&url={quote(str(plist_url), safe=':/?=&')}"
        if isinstance(plist_url, str) and plist_url
        else None
    )

    return {
        "artifacts": artifacts,
        "iosInstallUrl": ios_install_url,
    }


def _build_download_app_html(page_config: dict[str, Any]) -> str:
    css_version = int(DOWNLOAD_APP_CSS_PATH.stat().st_mtime)
    js_version = int(DOWNLOAD_APP_JS_PATH.stat().st_mtime)
    html = DOWNLOAD_APP_PAGE_PATH.read_text(encoding="utf-8")
    html = html.replace(
        "/static/download_app.css",
        f"/static/download_app.css?v={css_version}",
        1,
    )
    html = html.replace(
        "/static/download_app.js",
        f"/static/download_app.js?v={js_version}",
        1,
    )
    html = html.replace(
        "__DOWNLOAD_APP_CONFIG__",
        json.dumps(page_config, ensure_ascii=False),
        1,
    )
    return html


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


class ConversationTitleRequest(BaseModel):
    title: str
    credentialEnvelope: str | None = None


class ConversationAttachmentRef(BaseModel):
    id: str


class ConversationMessageRequest(BaseModel):
    content: str = ""
    attachments: list[ConversationAttachmentRef] = Field(default_factory=list)
    model: str | None = None
    credentialEnvelope: str | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    parallel_tool_calls: bool | None = None


class AttachmentDeleteRequest(BaseModel):
    conversationId: str
    credentialEnvelope: str | None = None


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


def _decorate_attachment_urls(payload: dict[str, Any], session_secret: str, scope_id: str) -> dict[str, Any]:
    def decorate_session(session: dict[str, Any]) -> None:
        conversation_id = session.get("id")
        messages = session.get("messages")
        if not isinstance(conversation_id, str) or not isinstance(messages, list):
            return
        for message in messages:
            if not isinstance(message, dict):
                continue
            attachments = message.get("attachments")
            if not isinstance(attachments, list):
                continue
            for attachment in attachments:
                if not isinstance(attachment, dict):
                    continue
                attachment_id = attachment.get("id")
                if not isinstance(attachment_id, str) or not attachment_id:
                    continue
                attachment["contentUrl"] = image_attachment_service.build_attachment_content_url(
                    session_secret=session_secret,
                    scope_id=scope_id,
                    conversation_id=conversation_id,
                    attachment_id=attachment_id,
                )

    sessions = payload.get("sessions")
    if isinstance(sessions, list):
        for session in sessions:
            if isinstance(session, dict):
                decorate_session(session)

    session = payload.get("session")
    if isinstance(session, dict):
        decorate_session(session)

    if isinstance(payload.get("id"), str) and isinstance(payload.get("messages"), list):
        decorate_session(payload)

    return payload


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


@app.exception_handler(ImageAttachmentError)
async def handle_image_attachment_error(_: Request, exc: ImageAttachmentError) -> JSONResponse:
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


@app.get("/download/app")
async def download_app_page(request: Request) -> HTMLResponse:
    return HTMLResponse(
        content=_build_download_app_html(_build_download_app_page_config(request)),
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )


@app.get("/download/app/{artifact}", name="download_app_artifact")
async def download_app_artifact(artifact: str) -> FileResponse:
    spec = DOWNLOAD_ARTIFACT_SPECS.get(artifact)
    if spec is None:
        raise HTTPException(status_code=404, detail="다운로드 파일을 찾을 수 없습니다.")

    artifact_path = _resolve_download_artifact_path(spec)
    if not artifact_path.exists() or not artifact_path.is_file():
        raise HTTPException(status_code=404, detail="다운로드 파일을 찾을 수 없습니다.")

    return FileResponse(
        path=artifact_path,
        media_type=spec.media_type,
        filename=artifact_path.name,
    )


@app.get("/api/models")
async def get_models() -> dict[str, list[dict[str, str]]]:
    return chat_service.get_models_payload()


@app.get("/api/conversations")
async def get_conversations(request: Request, response: Response) -> dict[str, Any]:
    browser_session = _resolve_browser_session_context(request)
    _apply_browser_session_cookie(response, browser_session)
    scope_id = auth_service.get_anonymous_history_scope(browser_session.conversation_scope_id)
    payload = conversation_service.get_state_payload(scope_id)
    return _decorate_attachment_urls(payload, browser_session.session_secret, scope_id)


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
    return _decorate_attachment_urls(
        conversation_service.get_state_payload(owner_context.scope_id),
        browser_session.session_secret,
        owner_context.scope_id,
    )


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
        "session": _decorate_attachment_urls(session, browser_session.session_secret, owner_context.scope_id),
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
    return {"session": _decorate_attachment_urls(session, browser_session.session_secret, owner_context.scope_id)}


@app.post("/api/conversations/{conversation_id}/delete")
async def delete_conversation(
    conversation_id: str,
    request: Request,
    response: Response,
    payload: ConversationStateRequest | None = None,
) -> dict[str, Any]:
    browser_session = _resolve_browser_session_context(request)
    owner_context, refreshed_envelope = await auth_service.resolve_history_scope(
        None if payload is None else payload.credentialEnvelope,
        browser_session.session_secret,
        browser_session.conversation_scope_id,
    )
    state_payload = conversation_service.delete_conversation(
        owner_context.scope_id,
        conversation_id,
    )
    _apply_browser_session_cookie(response, browser_session)
    _apply_credential_envelope_header(response, refreshed_envelope)
    return _decorate_attachment_urls(state_payload, browser_session.session_secret, owner_context.scope_id)


@app.post("/api/conversations/{conversation_id}/title")
async def update_conversation_title(
    conversation_id: str,
    request: Request,
    response: Response,
    payload: ConversationTitleRequest,
) -> dict[str, Any]:
    browser_session = _resolve_browser_session_context(request)
    title = conversation_service.validate_conversation_title(payload.title)
    owner_context, refreshed_envelope = await auth_service.resolve_history_scope(
        payload.credentialEnvelope,
        browser_session.session_secret,
        browser_session.conversation_scope_id,
    )
    session = conversation_service.update_conversation_title(
        owner_context.scope_id,
        conversation_id,
        title,
    )
    _apply_browser_session_cookie(response, browser_session)
    _apply_credential_envelope_header(response, refreshed_envelope)
    return {"session": _decorate_attachment_urls(session, browser_session.session_secret, owner_context.scope_id)}


@app.post("/api/uploads/images")
async def upload_image_attachment(
    request: Request,
    response: Response,
    file: UploadFile | None = File(None),
    conversationId: str = Form(...),
    credentialEnvelope: str = Form(...),
) -> dict[str, Any]:
    if not credentialEnvelope:
        raise CopilotAuthError(
            status_code=401,
            message="이 브라우저의 GitHub Copilot 자격 정보가 없습니다. 먼저 로그인하세요.",
            code="copilot_login_required",
        )

    browser_session = _resolve_browser_session_context(request)
    copilot_session, refreshed_envelope = await auth_service.resolve_session(
        credentialEnvelope,
        browser_session.session_secret,
    )
    if file is None:
        raise ImageAttachmentError(
            code="attachment_upload_required",
            message="업로드할 이미지가 없습니다.",
        )
    owner_context = auth_service.get_authenticated_history_owner(copilot_session)
    conversation_service.require_conversation(owner_context.scope_id, conversationId)

    attachment_id = f"att_{secrets.token_urlsafe(12)}"
    image_bytes = await file.read()
    stored_image = image_attachment_service.store_uploaded_image(
        attachment_id=attachment_id,
        image_bytes=image_bytes,
        declared_mime_type=file.content_type,
    )
    try:
        attachment = conversation_service.create_attachment(
            owner_context.scope_id,
            conversationId,
            attachment_id=attachment_id,
            original_filename=file.filename or f"{attachment_id}.jpg",
            mime_type=stored_image.mime_type,
            byte_size=stored_image.byte_size,
            width=stored_image.width,
            height=stored_image.height,
            storage_path=stored_image.storage_path,
        )
    except Exception:
        image_attachment_service.delete_uploaded_image(stored_image.storage_path)
        raise

    attachment["conversationId"] = conversationId
    attachment["contentUrl"] = image_attachment_service.build_attachment_content_url(
        session_secret=browser_session.session_secret,
        scope_id=owner_context.scope_id,
        conversation_id=conversationId,
        attachment_id=attachment["id"],
    )
    _apply_browser_session_cookie(response, browser_session)
    _apply_credential_envelope_header(response, refreshed_envelope)
    return {"attachment": attachment}


@app.delete("/api/uploads/images/{attachment_id}")
async def delete_image_attachment(
    attachment_id: str,
    request: Request,
    response: Response,
    payload: AttachmentDeleteRequest,
) -> dict[str, bool]:
    if not payload.credentialEnvelope:
        raise CopilotAuthError(
            status_code=401,
            message="이 브라우저의 GitHub Copilot 자격 정보가 없습니다. 먼저 로그인하세요.",
            code="copilot_login_required",
        )

    browser_session = _resolve_browser_session_context(request)
    copilot_session, refreshed_envelope = await auth_service.resolve_session(
        payload.credentialEnvelope,
        browser_session.session_secret,
    )
    owner_context = auth_service.get_authenticated_history_owner(copilot_session)
    attachment = conversation_service.get_attachment_record(
        owner_context.scope_id,
        payload.conversationId,
        attachment_id,
    )
    if attachment["messageId"] is not None:
        raise ConversationStateError(
            status_code=409,
            message="이미 전송에 사용된 첨부 이미지입니다. 다시 업로드하세요.",
            code="attachment_already_attached",
        )
    image_attachment_service.delete_uploaded_image(attachment["storagePath"])
    conversation_service.delete_pending_attachment(
        owner_context.scope_id,
        payload.conversationId,
        attachment_id,
    )
    _apply_browser_session_cookie(response, browser_session)
    _apply_credential_envelope_header(response, refreshed_envelope)
    return {"deleted": True}


@app.get("/api/conversations/{conversation_id}/attachments/{attachment_id}/content")
async def get_image_attachment_content(
    conversation_id: str,
    attachment_id: str,
    token: str,
    request: Request,
):
    browser_session = _resolve_browser_session_context(request)
    token_payload = image_attachment_service.validate_attachment_content_token(
        session_secret=browser_session.session_secret,
        conversation_id=conversation_id,
        attachment_id=attachment_id,
        token=token,
    )
    attachment = conversation_service.get_attachment_record_for_content(
        str(token_payload["scope_id"]),
        conversation_id,
        attachment_id,
    )
    image_attachment_service.open_attachment_file(attachment["storagePath"])
    response = FileResponse(
        path=attachment["storagePath"],
        media_type=attachment["mimeType"],
        headers={"Cache-Control": "private, max-age=300"},
    )
    _apply_browser_session_cookie(response, browser_session)
    return response


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

    content, attachment_ids = conversation_service.validate_message_input(
        payload.content,
        [attachment.model_dump() for attachment in payload.attachments],
    )

    browser_session = _resolve_browser_session_context(request)
    copilot_session, refreshed_envelope = await auth_service.resolve_session(
        payload.credentialEnvelope,
        browser_session.session_secret,
    )
    owner_context = auth_service.get_authenticated_history_owner(copilot_session)
    conversation_payload = conversation_service.get_conversation_payload(owner_context.scope_id, conversation_id)
    model_id = chat_service.resolve_model(payload.model or conversation_payload.get("model"))
    if attachment_ids:
        chat_service.ensure_model_supports_vision(model_id)
    turn = conversation_service.begin_turn(
        owner_context.scope_id,
        conversation_id,
        content=content,
        attachment_ids=attachment_ids,
        model=model_id,
    )
    provider_messages = chat_service.build_provider_messages(
        turn.prior_messages,
        turn.new_user_message,
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
                messages=provider_messages,
                session=copilot_session,
                initiator_messages=provider_messages,
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