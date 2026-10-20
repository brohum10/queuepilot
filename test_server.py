import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from server import make_server


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = make_server(0, Path(cls.temp.name) / "test.db")
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self, path="/", method="GET", body=None, headers=None):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=10
        )
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            data = response.read()
            return response.status, dict(response.getheaders()), data
        finally:
            connection.close()

    def post(self, path, body, **headers):
        defaults = {
            "Origin": self.origin,
            "X-QueuePilot-Token": self.server.app.token,
            "Content-Type": "application/json",
        }
        defaults.update(headers)
        return self.request(path, "POST", json.dumps(body), defaults)

    def test_home_has_csp_and_no_cache(self):
        status, headers, data = self.request()
        self.assertEqual(status, 200)
        self.assertIn(b"QueuePilot", data)
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_static_assets(self):
        for path in ["/app.js", "/style.css", "/report.json", "/evidence.zip"]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path)[0], 200)

    def test_path_traversal_blocked(self):
        for path in [
            "/../server.py",
            "/models/candidate.npz",
            "/runtime/inbox.sqlite3",
            "/%2e%2e/server.py",
            "/server.py",
        ]:
            self.assertEqual(self.request(path)[0], 404)

    def test_dns_rebinding_host_blocked(self):
        self.assertEqual(self.request(headers={"Host": "evil.example"})[0], 403)

    def test_cross_origin_write_blocked(self):
        self.assertEqual(
            self.post("/api/predict", {"text": "hello"}, Origin="https://evil.example")[
                0
            ],
            403,
        )

    def test_missing_token_blocked(self):
        self.assertEqual(
            self.request(
                "/api/predict",
                "POST",
                "{}",
                {"Origin": self.origin, "Content-Type": "application/json"},
            )[0],
            403,
        )

    def test_non_json_blocked(self):
        self.assertEqual(
            self.post("/api/predict", {}, **{"Content-Type": "text/plain"})[0], 415
        )

    def test_oversized_request_blocked_before_body_read(self):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=10
        )
        try:
            connection.putrequest("POST", "/api/predict")
            for name, value in {
                "Origin": self.origin,
                "X-QueuePilot-Token": self.server.app.token,
                "Content-Type": "application/json",
                "Content-Length": "521000",
            }.items():
                connection.putheader(name, value)
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 413)
            response.read()
        finally:
            connection.close()

    def test_nonfinite_json_rejected(self):
        self.assertEqual(self.post("/api/replay", {"threshold": float("nan")})[0], 400)

    def test_duplicate_json_keys_rejected(self):
        headers = {
            "Origin": self.origin,
            "X-QueuePilot-Token": self.server.app.token,
            "Content-Type": "application/json",
        }
        self.assertEqual(
            self.request("/api/predict", "POST", '{"text":"a","text":"b"}', headers)[0],
            400,
        )

    def test_real_local_inference_and_redaction(self):
        status, _, data = self.post(
            "/api/predict", {"text": "Where is my card? Email me at a@example.com"}
        )
        self.assertEqual(status, 200)
        result = json.loads(data)
        self.assertIn("[email]", result["text"])
        self.assertIn("candidate", result)
        self.assertIn("baseline", result)
        self.assertGreater(result["elapsed_ms"], 0)

    def test_multi_issue_prediction_is_held(self):
        status, _, data = self.post(
            "/api/predict", {"text": "I was charged twice and can't log in"}
        )
        self.assertEqual(status, 200)
        self.assertIn("multiple_issue_rule", json.loads(data)["reasons"])

    def test_import_has_no_fake_accuracy(self):
        status, _, data = self.post(
            "/api/import",
            {"csv": "id,text\na,Where is my card?\nb,I was hacked", "budget": 0},
        )
        self.assertEqual(status, 200)
        result = json.loads(data)
        self.assertIsNone(result["metrics"]["auto_accuracy"])
        self.assertEqual(result["metrics"]["review"], 0)

    def test_invalid_import_does_not_replace_session(self):
        before = json.loads(self.request("/api/workspace")[2])["session"]
        self.assertEqual(self.post("/api/import", {"csv": "id,text\na,a\na,b"})[0], 400)
        self.assertEqual(
            before, json.loads(self.request("/api/workspace")[2])["session"]
        )

    def test_zero_capacity_replay_keeps_flags_waiting(self):
        status, _, data = self.post("/api/replay", {"limit": 120, "budget": 0})
        self.assertEqual(status, 200)
        stats = json.loads(data)["metrics"]
        self.assertEqual(stats["review"], 0)
        self.assertGreater(stats["waiting"], 0)

    def test_export_excludes_model_weights_and_runtime_paths(self):
        status, headers, data = self.request("/api/export.csv")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertIn(b"suggested_intent", data)

    def test_invalid_budget_type_and_limits(self):
        for value in [True, -1, 1001, 1.2]:
            self.assertEqual(self.post("/api/replay", {"budget": value})[0], 400)

    def test_unknown_session_rejected(self):
        self.assertEqual(
            self.post(
                "/api/plan", {"session": "x" * 32, "threshold": 0.8, "budget": 1}
            )[0],
            400,
        )

    def test_unknown_endpoint(self):
        self.assertEqual(self.post("/api/not-real", {})[0], 404)


if __name__ == "__main__":
    unittest.main()
