import unittest
from tt_response_errors import response_error


class ResponseErrorTests(unittest.TestCase):
    def test_nested_error(self):
        result = response_error({"type": "error", "error": {"code": "unsupported_tool", "param": "tools[0]", "message": "web_search is not supported"}})
        self.assertIn("unsupported_tool", result)
        self.assertIn("联网", result)

    def test_top_level_error(self):
        self.assertIn("server_error", response_error({"type": "error", "code": "server_error", "message": "internal error"}))

    def test_no_message_or_input_leak(self):
        result = response_error({"type": "error", "error": {"code": "invalid_request_error", "message": "PRIVATE_INPUT sk-secret https://private.example", "param": "secret value"}})
        self.assertNotIn("PRIVATE_INPUT", result)
        self.assertNotIn("sk-secret", result)
        self.assertNotIn("private.example", result)

    def test_response_failed_and_incomplete(self):
        self.assertIn("rate_limit", response_error({"type": "response.failed", "response": {"error": {"code": "rate_limit_exceeded"}}}))
        self.assertTrue(response_error({"type": "response.incomplete", "response": {}}))


if __name__ == "__main__":
    unittest.main()
