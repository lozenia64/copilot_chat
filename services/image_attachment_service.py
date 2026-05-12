from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from PIL import Image, UnidentifiedImageError


BASE_DIR = Path(__file__).resolve().parent.parent
UPLOADS_ROOT = BASE_DIR / ".uploads" / "images"
MAX_UPLOADED_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg"}
ATTACHMENT_URL_TTL_SECONDS = 600


class ImageAttachmentError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


@dataclass(slots=True)
class StoredImageMetadata:
    mime_type: str
    width: int
    height: int
    byte_size: int
    storage_path: str


class ImageAttachmentService:
    def __init__(
        self,
        *,
        uploads_root: str | os.PathLike[str] | None = None,
        content_url_ttl_seconds: int = ATTACHMENT_URL_TTL_SECONDS,
    ) -> None:
        self.uploads_root = Path(uploads_root) if uploads_root else UPLOADS_ROOT
        self.content_url_ttl_seconds = max(int(content_url_ttl_seconds), 1)
        self.uploads_root.mkdir(parents=True, exist_ok=True)

    def validate_uploaded_image_bytes(
        self,
        image_bytes: bytes,
        *,
        declared_mime_type: str | None,
    ) -> tuple[int, int, str]:
        byte_size = len(image_bytes)
        if byte_size <= 0:
            raise ImageAttachmentError(
                code="attachment_upload_required",
                message="업로드할 이미지가 없습니다.",
            )
        if byte_size > MAX_UPLOADED_IMAGE_BYTES:
            raise ImageAttachmentError(
                code="attachment_file_too_large",
                message="이미지 1개당 5MB 이하 업로드만 허용됩니다.",
            )

        normalized_mime = (declared_mime_type or "").strip().lower()
        if normalized_mime not in ALLOWED_IMAGE_MIME_TYPES:
            raise ImageAttachmentError(
                code="attachment_image_only",
                message="이미지 파일만 첨부할 수 있습니다.",
            )

        try:
            with Image.open(io := self._bytes_to_image_stream(image_bytes)) as image:
                image.verify()
            with Image.open(self._bytes_to_image_stream(image_bytes)) as image:
                width, height = image.size
                actual_mime = Image.MIME.get(image.format or "", "").lower()
        except (UnidentifiedImageError, OSError, ValueError):
            raise ImageAttachmentError(
                code="attachment_decode_failed",
                message="이미지 파일을 처리할 수 없습니다. 다른 파일을 선택하세요.",
            )

        if actual_mime not in ALLOWED_IMAGE_MIME_TYPES:
            raise ImageAttachmentError(
                code="attachment_image_only",
                message="이미지 파일만 첨부할 수 있습니다.",
            )

        if width <= 0 or height <= 0:
            raise ImageAttachmentError(
                code="attachment_decode_failed",
                message="이미지 파일을 처리할 수 없습니다. 다른 파일을 선택하세요.",
            )

        return width, height, actual_mime

    def store_uploaded_image(
        self,
        *,
        attachment_id: str,
        image_bytes: bytes,
        declared_mime_type: str | None = None,
        created_at: float | None = None,
    ) -> StoredImageMetadata:
        created_ts = time.time() if created_at is None else float(created_at)
        width, height, mime_type = self.validate_uploaded_image_bytes(
            image_bytes,
            declared_mime_type=declared_mime_type,
        )
        storage_path = self._build_storage_path(attachment_id, created_ts)
        storage_path.parent.mkdir(parents=True, exist_ok=True)
        storage_path.write_bytes(image_bytes)
        return StoredImageMetadata(
            mime_type=mime_type,
            width=width,
            height=height,
            byte_size=len(image_bytes),
            storage_path=str(storage_path),
        )

    def open_attachment_file(self, storage_path: str | os.PathLike[str]) -> bytes:
        path = Path(storage_path)
        if not path.exists() or not path.is_file():
            raise ImageAttachmentError(
                code="attachment_not_found",
                message="첨부 이미지를 찾을 수 없습니다. 다시 업로드하세요.",
                status_code=404,
            )
        return path.read_bytes()

    def delete_uploaded_image(self, storage_path: str | os.PathLike[str]) -> None:
        self.delete_file_if_exists(storage_path)

    def delete_file_if_exists(self, storage_path: str | os.PathLike[str]) -> None:
        path = Path(storage_path)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise ImageAttachmentError(
                code="attachment_delete_failed",
                message="첨부 이미지를 삭제하지 못했습니다. 다시 시도하세요.",
                status_code=500,
            ) from exc

    def build_attachment_content_url(
        self,
        *,
        session_secret: str,
        conversation_id: str,
        attachment_id: str,
    ) -> str:
        token = self.build_attachment_content_token(
            session_secret=session_secret,
            conversation_id=conversation_id,
            attachment_id=attachment_id,
        )
        query = urlencode({"token": token})
        return f"/api/conversations/{conversation_id}/attachments/{attachment_id}/content?{query}"

    def build_attachment_content_token(
        self,
        *,
        session_secret: str,
        conversation_id: str,
        attachment_id: str,
        expires_at: float | None = None,
    ) -> str:
        exp = int(expires_at if expires_at is not None else time.time() + self.content_url_ttl_seconds)
        payload = {
            "attachment_id": attachment_id,
            "conversation_id": conversation_id,
            "exp": exp,
        }
        payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        payload_b64 = self._urlsafe_b64encode(payload_bytes)
        signature = self._sign_payload(session_secret, payload_b64)
        return f"{payload_b64}.{signature}"

    def validate_attachment_content_token(
        self,
        *,
        session_secret: str,
        conversation_id: str,
        attachment_id: str,
        token: str,
    ) -> None:
        if not isinstance(token, str) or "." not in token:
            raise ImageAttachmentError(
                code="attachment_access_denied",
                message="이 이미지에 접근할 수 없습니다.",
                status_code=403,
            )
        payload_b64, provided_signature = token.split(".", 1)
        expected_signature = self._sign_payload(session_secret, payload_b64)
        if not hmac.compare_digest(provided_signature, expected_signature):
            raise ImageAttachmentError(
                code="attachment_access_denied",
                message="이 이미지에 접근할 수 없습니다.",
                status_code=403,
            )
        try:
            payload_raw = self._urlsafe_b64decode(payload_b64)
            payload = json.loads(payload_raw)
        except (ValueError, json.JSONDecodeError):
            raise ImageAttachmentError(
                code="attachment_access_denied",
                message="이 이미지에 접근할 수 없습니다.",
                status_code=403,
            )

        if payload.get("attachment_id") != attachment_id or payload.get("conversation_id") != conversation_id:
            raise ImageAttachmentError(
                code="attachment_access_denied",
                message="이 이미지에 접근할 수 없습니다.",
                status_code=403,
            )
        expires_at = int(payload.get("exp") or 0)
        if expires_at < int(time.time()):
            raise ImageAttachmentError(
                code="attachment_access_denied",
                message="이 이미지에 접근할 수 없습니다.",
                status_code=403,
            )

    def attachment_to_data_url(self, storage_path: str | os.PathLike[str]) -> str:
        image_bytes = self.open_attachment_file(storage_path)
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:image/jpeg;base64,{encoded}"

    def _build_storage_path(self, attachment_id: str, created_at: float) -> Path:
        created = time.localtime(created_at)
        return self.uploads_root / time.strftime("%Y/%m/%d", created) / f"{attachment_id}.jpg"

    def _sign_payload(self, session_secret: str, payload_b64: str) -> str:
        digest = hmac.new(
            session_secret.encode("utf-8"),
            payload_b64.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return self._urlsafe_b64encode(digest)

    def _bytes_to_image_stream(self, image_bytes: bytes):
        from io import BytesIO

        return BytesIO(image_bytes)

    def _urlsafe_b64encode(self, raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def _urlsafe_b64decode(self, raw: str) -> bytes:
        padding = "=" * (-len(raw) % 4)
        return base64.urlsafe_b64decode((raw + padding).encode("ascii"))