import http.client
import json
import threading
import unittest

from core.settings import load_settings
from server.app import make_server
from tests.helpers import make_project


class P7HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = make_project({})
        cls.httpd = make_server(load_settings(cls.root), port=0)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def call(self, method, path, payload=None, custom=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if custom and method == "POST":
            headers["X-Info-Hub"] = "1"
        body = json.dumps(payload) if payload is not None else None
        conn.request(method, path, body=body, headers=headers)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, json.loads(data or b"{}")

    def test_llm_status_reports_none_when_unconfigured(self):
        status, body = self.call("GET", "/api/llm-status")
        self.assertEqual(status, 200)
        self.assertEqual(body["source"], "none")

    def test_generate_without_provider_is_503(self):
        status, body = self.call("POST", "/api/sources/generate", {"url": "https://x.example.com/", "name": "x"})
        self.assertEqual(status, 503)
        self.assertIn("providers", body["error"])

    def test_post_without_header_rejected_for_new_routes(self):
        status, _ = self.call("POST", "/api/sources/create", {"source": {}}, custom=False)
        self.assertEqual(status, 403)

    def test_drafts_list_and_missing_draft_404(self):
        status, body = self.call("GET", "/api/drafts")
        self.assertEqual(status, 200)
        self.assertEqual(body["drafts"], [])
        status, _ = self.call("GET", "/api/drafts/nope_nope")
        self.assertEqual(status, 404)
        status, _ = self.call("POST", "/api/drafts/nope_nope/confirm", {})
        self.assertEqual(status, 404)

    def test_unknown_route_and_wrong_method(self):
        status, _ = self.call("GET", "/api/nothing-here")
        self.assertEqual(status, 404)
        status, _ = self.call("GET", "/api/sources/create")
        self.assertEqual(status, 405)

    def test_create_form_over_http(self):
        form = {"id": "http_src", "name": "HTTP源", "category": "news", "kind": "feed", "adapter": "rss",
                "url": "https://news.example.com/feed"}
        status, body = self.call("POST", "/api/sources/create", {"source": form})
        self.assertEqual(status, 200, body)
        status, listing = self.call("GET", "/api/sources")
        self.assertIn("http_src", [s["id"] for s in listing["sources"]])


if __name__ == "__main__":
    unittest.main()
