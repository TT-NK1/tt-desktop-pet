import copy
import json
import threading
import unittest

import httpx

from tt_search_backend import TavilyProvider, arguments, tool_loop


class SearchTests(unittest.TestCase):
    def test_arguments(self):
        self.assertEqual(arguments({"query": "news"}), ("news", 5))
        for value in (0, 11, True):
            with self.assertRaises(ValueError):
                arguments({"query": "news", "max_results": value})
        with self.assertRaises(ValueError):
            arguments({"query": "Bearer SECRET"})

    def test_missing_key(self):
        self.assertEqual(TavilyProvider("").search({"query": "x"})["status"], "not_configured")

    def test_provider_metadata_and_privacy(self):
        def handler(request):
            data = json.loads(request.content)
            self.assertEqual(data["query"], "public news")
            self.assertNotIn("input", data)
            self.assertEqual(request.headers["authorization"], "Bearer fake")
            return httpx.Response(200, json={"results": [{"title": "Title", "url": "https://example.com/a", "content": "x" * 2000}]})
        result = TavilyProvider("fake", transport=httpx.MockTransport(handler)).search({"query": "public news"})
        self.assertEqual(result["status"], "ok")
        self.assertLessEqual(len(result["results"][0]["summary"]), 1000)
        self.assertIn("retrieved_at", result["results"][0])
        self.assertNotIn("published_at", result["results"][0])

    def test_provider_failures(self):
        for code in (401, 429, 500):
            transport = httpx.MockTransport(lambda request: httpx.Response(code))
            self.assertEqual(TavilyProvider("fake", transport=transport).search({"query": "x"})["status"], "provider_error")
        def unavailable(request):
            raise httpx.ConnectError("private diagnostic detail")
        result = TavilyProvider("fake", transport=httpx.MockTransport(unavailable)).search({"query": "x"})
        self.assertEqual(result["status"], "network_error")
        self.assertNotIn("private", str(result))

    def flow(self, rounds=1, approved=True, status="ok", repeat=False):
        requests, calls, emitted = [], [], []
        def respond(payload, emit):
            requests.append(copy.deepcopy(payload))
            if len(requests) <= rounds:
                return {"status": "completed", "output": [
                    {"type": "reasoning", "id": "reasoning-item", "summary": []},
                    {"type": "function_call", "namespace": "research", "name": "web_search",
                     "call_id": "same" if repeat else f"call{len(requests)}", "arguments": '{"query":"public news"}'}]}
            emit("Answer")
            return {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Answer"}]}]}
        def search(value):
            calls.append(value)
            return {"status": status, "results": [{"url": "https://example.com/a"}] if status == "ok" else []}
        answer = tool_loop({"input": [{"role": "user", "content": "news"}]}, respond, search,
                           lambda query: approved, threading.Event(), emitted.append)
        return requests, calls, emitted, answer

    def test_multiround_ids_reasoning_and_sources(self):
        requests, calls, emitted, answer = self.flow(rounds=2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(requests[1]["input"][-1]["call_id"], "call1")
        self.assertEqual(requests[1]["input"][-2]["type"], "function_call")
        self.assertEqual(requests[1]["input"][-3]["type"], "reasoning")
        self.assertIn("https://example.com/a", answer)
        self.assertEqual(answer, "".join(emitted))

    def test_denied_and_failed_result_return(self):
        for approved, status, expected in ((False, "ok", "user_declined"), (True, "network_error", "network_error")):
            requests, calls, emitted, answer = self.flow(approved=approved, status=status)
            self.assertIn(expected, requests[1]["input"][-1]["output"])
            self.assertEqual(len(calls), int(approved))

    def test_call_limit(self):
        requests, calls, emitted, answer = self.flow(rounds=3)
        self.assertEqual(len(calls), 3)
        self.assertEqual(requests[-1]["tool_choice"], "none")

    def test_repeated_id_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            self.flow(rounds=2, repeat=True)

    def test_cancelled(self):
        event = threading.Event()
        event.set()
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            tool_loop({"input": []}, None, None, None, event, None)


if __name__ == "__main__":
    unittest.main()
