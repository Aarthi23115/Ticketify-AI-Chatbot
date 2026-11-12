"""Tests for the Ticketify AI Chatbot. Gemini is mocked, so no API key is needed.

Run with:  python -m unittest -v
"""
import unittest
from unittest import mock

import app as chatbot


class FakeResponse:
    def __init__(self, text):
        self.text = text


class ChatbotTests(unittest.TestCase):
    def setUp(self):
        chatbot.app.config["TESTING"] = True
        self.client = chatbot.app.test_client()
        chatbot._requests_by_ip.clear()

    def test_bundled_context_is_loaded(self):
        for key, text in chatbot.CONTEXT.items():
            self.assertFalse(text.endswith("was not found."), f"{key} missing from context/")
        self.assertIn("class Ticket", chatbot.CONTEXT["models"])

    def test_home_page_renders(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Ticketify AI Chatbot", response.data)

    def test_missing_api_key_message(self):
        with mock.patch.object(chatbot, "client", None):
            response = self.client.post("/", data={"question": "What is Ticketify?"})
        self.assertIn(b"Gemini API key is missing", response.data)

    def test_question_is_answered_from_project_context(self):
        fake = mock.Mock()
        fake.models.generate_content.return_value = FakeResponse("Bookings are stored in the Booking model.")
        with mock.patch.object(chatbot, "client", fake):
            response = self.client.post("/", data={"question": "Where are bookings stored?"})
        self.assertIn(b"Bookings are stored in the Booking model.", response.data)
        prompt = fake.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("Where are bookings stored?", prompt)
        self.assertIn("PROJECT MODELS.PY:", prompt)
        self.assertIn("class Booking", prompt)

    def test_answer_html_is_escaped(self):
        fake = mock.Mock()
        fake.models.generate_content.return_value = FakeResponse("<script>alert(1)</script>")
        with mock.patch.object(chatbot, "client", fake):
            response = self.client.post("/", data={"question": "<b>hi</b>"})
        self.assertNotIn(b"<script>alert(1)</script>", response.data)
        self.assertIn(b"&lt;script&gt;", response.data)

    def test_rate_limit(self):
        fake = mock.Mock()
        fake.models.generate_content.return_value = FakeResponse("ok")
        with mock.patch.object(chatbot, "client", fake), \
                mock.patch.object(chatbot, "RATE_LIMIT_PER_MINUTE", 2):
            for _ in range(2):
                self.client.post("/", data={"question": "q"})
            response = self.client.post("/", data={"question": "q"})
        self.assertIn(b"Too many questions", response.data)
        self.assertEqual(fake.models.generate_content.call_count, 2)

    def test_gemini_error_is_handled(self):
        fake = mock.Mock()
        fake.models.generate_content.side_effect = RuntimeError("boom")
        with mock.patch.object(chatbot, "client", fake), self.assertLogs("ticketify-chatbot", "ERROR"):
            response = self.client.post("/", data={"question": "q"})
        self.assertIn(b"Sorry, something went wrong", response.data)

    def test_long_question_is_truncated(self):
        fake = mock.Mock()
        fake.models.generate_content.return_value = FakeResponse("ok")
        with mock.patch.object(chatbot, "client", fake):
            self.client.post("/", data={"question": "x" * 5000})
        prompt = fake.models.generate_content.call_args.kwargs["contents"]
        self.assertNotIn("x" * (chatbot.MAX_QUESTION_LENGTH + 1), prompt)

    def test_healthz(self):
        response = self.client.get("/healthz")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["readme_found"])


if __name__ == "__main__":
    unittest.main()
