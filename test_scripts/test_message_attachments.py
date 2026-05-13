from _path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from services.conversation_service import ConversationService, ConversationStateError


class ConnectedRequest:
    async def is_disconnected(self):
        return False


class FakeSseStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._iterator = iter(())

    def __aiter__(self):
        self._iterator = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class MessageAttachmentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.service = ConversationService(
            history_db_path=Path(self.temp_dir.name) / "history.sqlite3",
            cleanup_interval_seconds=0,
        )
        self.scope_id = "user:test"
        conversation = self.service.create_conversation(self.scope_id, "gpt-5.4")
        self.conversation_id = conversation["id"]
        self.storage_path = Path(self.temp_dir.name) / "attachment.jpg"
        self.storage_path.write_bytes(b"jpeg-bytes")

    def _create_attachment(self, attachment_id: str = "att_test") -> dict:
        return self.service.create_attachment(
            self.scope_id,
            self.conversation_id,
            attachment_id=attachment_id,
            original_filename="attachment.jpg",
            mime_type="image/jpeg",
            byte_size=10,
            width=32,
            height=32,
            storage_path=str(self.storage_path),
        )

    def test_attachment_only_message_is_persisted_as_visible_user_message(self):
        attachment = self._create_attachment()

        content, attachment_ids = self.service.validate_message_input("   ", [{"id": attachment["id"]}])
        turn = self.service.begin_turn(
            self.scope_id,
            self.conversation_id,
            content=content,
            attachment_ids=attachment_ids,
            model="gpt-5.4",
        )

        self.assertEqual(content, "")
        self.assertEqual(attachment_ids, [attachment["id"]])
        self.assertEqual(turn.new_user_message["attachments"][0]["id"], attachment["id"])

        payload = self.service.get_conversation_payload(self.scope_id, self.conversation_id)
        self.assertEqual(len(payload["messages"]), 1)
        self.assertEqual(payload["messages"][0]["role"], "user")
        self.assertEqual(payload["messages"][0]["content"], "")
        self.assertEqual(payload["messages"][0]["attachments"][0]["id"], attachment["id"])

    def test_delete_pending_attachment_rejects_already_attached_image(self):
        attachment = self._create_attachment()
        self.service.begin_turn(
            self.scope_id,
            self.conversation_id,
            content="",
            attachment_ids=[attachment["id"]],
            model="gpt-5.4",
        )

        with self.assertRaises(ConversationStateError) as raised:
            self.service.delete_pending_attachment(
                self.scope_id,
                self.conversation_id,
                attachment["id"],
            )

        self.assertEqual(raised.exception.code, "attachment_already_attached")

    def test_existing_db_upgrades_and_persists_assistant_sources(self):
        legacy_db_path = Path(self.temp_dir.name) / "legacy-history.sqlite3"
        connection = sqlite3.connect(legacy_db_path)
        connection.executescript(
            """
            CREATE TABLE conversation_scopes (
                scope_id TEXT PRIMARY KEY,
                active_conversation_id TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY,
                scope_id TEXT NOT NULL,
                title TEXT NOT NULL,
                model TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE conversation_messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                state TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE conversation_attachments (
                id TEXT PRIMARY KEY,
                scope_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                message_id TEXT,
                original_filename TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                byte_size INTEGER NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                storage_path TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            """
        )
        connection.commit()
        connection.close()

        service = ConversationService(
            history_db_path=legacy_db_path,
            cleanup_interval_seconds=0,
        )

        with service._repository._connection() as connection:
            columns = [str(row["name"]) for row in connection.execute("PRAGMA table_info(conversation_messages)").fetchall()]
        self.assertIn("sources_json", columns)

        conversation = service.create_conversation(self.scope_id, "gpt-5.4")
        turn = service.begin_turn(
            self.scope_id,
            conversation["id"],
            content="웹 검색 결과 정리해줘",
            attachment_ids=[],
            model="gpt-5.4",
        )
        service._repository.finalize_assistant_message(
            self.scope_id,
            conversation["id"],
            turn.assistant_message_id,
            content="정리된 답변",
            state="complete",
            sources=[
                {"title": "첫 링크", "url": "https://example.com/a"},
                {"title": "위험 링크", "url": "javascript:alert(1)"},
                {"title": "FTP 링크", "url": "ftp://example.com/file"},
                {"title": "중복 링크", "url": "https://example.com/a"},
                {"title": "둘째 링크", "url": "http://example.com/b"},
            ],
        )

        payload = service.get_conversation_payload(self.scope_id, conversation["id"])
        self.assertEqual(
            payload["messages"][-1]["sources"],
            [
                {"title": "첫 링크", "url": "https://example.com/a"},
                {"title": "둘째 링크", "url": "http://example.com/b"},
            ],
        )


class PersistStreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.service = ConversationService(
            history_db_path=Path(self.temp_dir.name) / "history.sqlite3",
            cleanup_interval_seconds=0,
        )
        self.scope_id = "user:test"
        conversation = self.service.create_conversation(self.scope_id, "gpt-5.4")
        self.conversation_id = conversation["id"]

    async def test_search_status_event_does_not_become_persisted_assistant_content_on_failure(self):
        turn = self.service.begin_turn(
            self.scope_id,
            self.conversation_id,
            content="최신 웹 검색 결과 알려줘",
            attachment_ids=[],
            model="gpt-5.4",
        )
        expected_sources = [{"title": "첫 링크", "url": "https://example.com/a"}]
        stream = FakeSseStream(
            [
                b'data: {"type":"assistant_status","status":"web_searching"}\n\n',
                'data: {"type":"assistant_sources","sources":[{"title":"첫 링크","url":"https://example.com/a"}]}\n\n'.encode("utf-8"),
                'data: {"code":"copilot_chat_stream_failed","message":"응답 생성 실패"}\n\n'.encode("utf-8"),
                b'data: [DONE]\n\n',
            ]
        )

        emitted_chunks = []
        async for chunk in self.service.persist_stream(
            request=ConnectedRequest(),
            scope_id=self.scope_id,
            conversation_id=self.conversation_id,
            assistant_message_id=turn.assistant_message_id,
            stream=stream,
        ):
            emitted_chunks.append(chunk)

        self.assertEqual(emitted_chunks[-1], b"data: [DONE]\n\n")

        payload = self.service.get_conversation_payload(self.scope_id, self.conversation_id)
        assistant_message = payload["messages"][-1]
        self.assertEqual(assistant_message["content"], "응답 생성 실패")
        self.assertEqual(assistant_message["status"], "partial")
        self.assertEqual(assistant_message["sources"], expected_sources)
        self.assertNotIn("웹 검색 중", assistant_message["content"])


if __name__ == "__main__":
    unittest.main()