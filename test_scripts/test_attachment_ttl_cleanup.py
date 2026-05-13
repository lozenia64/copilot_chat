from _path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest import mock

from services.conversation_service import ConversationService


class AttachmentTtlCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.service = ConversationService(
            history_db_path=Path(self.temp_dir.name) / "history.sqlite3",
            ttl_seconds=1,
            cleanup_interval_seconds=0,
        )
        self.scope_id = "user:test"
        conversation = self.service.create_conversation(self.scope_id, "gpt-5.4")
        self.conversation_id = conversation["id"]
        self.storage_path = Path(self.temp_dir.name) / "expired.jpg"
        self.storage_path.write_bytes(b"expired-image")
        self.attachment = self.service.create_attachment(
            self.scope_id,
            self.conversation_id,
            attachment_id="att_expired",
            original_filename="expired.jpg",
            mime_type="image/jpeg",
            byte_size=13,
            width=16,
            height=16,
            storage_path=str(self.storage_path),
        )
        self._expire_conversation()

    def _expire_conversation(self):
        expired_at = time.time() - 10
        with self.service._repository._connection() as connection:
            connection.execute(
                """
                UPDATE conversations
                SET created_at = ?, updated_at = ?
                WHERE id = ? AND scope_id = ?
                """,
                (expired_at, expired_at, self.conversation_id, self.scope_id),
            )
            connection.execute(
                """
                UPDATE conversation_attachments
                SET created_at = ?, updated_at = ?
                WHERE id = ? AND conversation_id = ? AND scope_id = ?
                """,
                (expired_at, expired_at, self.attachment["id"], self.conversation_id, self.scope_id),
            )

    def test_cleanup_deletes_expired_attachment_file(self):
        payload = self.service.get_state_payload(self.scope_id)

        self.assertEqual(payload["sessions"], [])
        self.assertFalse(self.storage_path.exists())

    def test_cleanup_continues_when_attachment_file_delete_fails(self):
        with mock.patch("services.conversation_service.Path.unlink", side_effect=OSError("disk busy")):
            payload = self.service.get_state_payload(self.scope_id)

        self.assertEqual(payload["sessions"], [])
        self.assertTrue(self.storage_path.exists())


if __name__ == "__main__":
    unittest.main()