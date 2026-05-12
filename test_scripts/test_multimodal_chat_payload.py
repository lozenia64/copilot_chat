from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from PIL import Image

from services.copilot_chat import CopilotChatRequestError, CopilotChatService


ROOT_DIR = Path(__file__).resolve().parents[1]


class MultimodalChatPayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.image_path = Path(self.temp_dir.name) / "sample.jpg"
        image = Image.new("RGB", (8, 8), color=(255, 0, 0))
        image.save(self.image_path, format="JPEG")
        self.service = CopilotChatService(
            config_path=ROOT_DIR / "litellm_config.yaml",
            default_model="gpt-5.4",
        )

    def test_build_provider_messages_summarizes_history_and_embeds_current_image(self):
        provider_messages = self.service.build_provider_messages(
            [
                {
                    "role": "user",
                    "content": "이전 질문",
                    "attachments": [{"fileName": "older.jpg"}],
                },
                {"role": "assistant", "content": "이전 답변"},
            ],
            {
                "content": "이 이미지를 설명해줘",
                "attachments": [{"fileName": "fresh.jpg", "storagePath": str(self.image_path)}],
            },
        )

        self.assertEqual(
            provider_messages[0]["content"],
            "이전 질문\n\n[사용자가 이미지 1개를 첨부함: older.jpg]",
        )
        self.assertEqual(provider_messages[1], {"role": "assistant", "content": "이전 답변"})
        self.assertEqual(provider_messages[2]["role"], "user")
        self.assertEqual(provider_messages[2]["content"][0], {"type": "text", "text": "이 이미지를 설명해줘"})
        self.assertEqual(provider_messages[2]["content"][1]["type"], "image_url")
        self.assertTrue(
            provider_messages[2]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        )

    @mock.patch("services.copilot_chat.litellm.supports_vision", return_value=False)
    def test_ensure_model_supports_vision_rejects_nonvision_model(self, _mock_supports_vision):
        with self.assertRaises(CopilotChatRequestError) as raised:
            self.service.ensure_model_supports_vision("gpt-5.4")

        self.assertEqual(raised.exception.code, "chat_model_not_vision_capable")


if __name__ == "__main__":
    unittest.main()