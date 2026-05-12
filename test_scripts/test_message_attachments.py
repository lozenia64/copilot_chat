from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from services.conversation_service import ConversationService, ConversationStateError


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


if __name__ == "__main__":
    unittest.main()