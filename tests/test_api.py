import json
import unittest

from server import api
from tests import support


class QueryParserTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(api.parse_query(""), {})

    def test_pairs(self):
        self.assertEqual(api.parse_query("a=1&b=2"), {"a": "1", "b": "2"})

    def test_flag_without_value(self):
        self.assertEqual(api.parse_query("debug"), {"debug": ""})

    def test_percent_and_plus_decoding(self):
        self.assertEqual(api.parse_query("q=return+loss"), {"q": "return loss"})
        self.assertEqual(api.parse_query("q=a%2Fb"), {"q": "a/b"})

    def test_later_duplicate_wins(self):
        self.assertEqual(api.parse_query("a=1&a=2"), {"a": "2"})

    def test_value_may_contain_equals(self):
        self.assertEqual(api.parse_query("q=a=b"), {"q": "a=b"})


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = support.build_app()
        cls.client = support.Client(cls.app)

    def test_health(self):
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["counts"]["documents"], 40)
        self.assertEqual(body["counts"]["datasets"], 16)

    def test_json_headers(self):
        res = self.client.get("/api/health")
        self.assertTrue(res.headers["content-type"].startswith("application/json"))
        self.assertEqual(res.headers["content-length"], str(len(res.body)))
        self.assertEqual(res.headers["x-content-type-options"], "nosniff")
        self.assertTrue(res.etag)

    def test_etag_round_trip_gives_304_with_no_body(self):
        first = self.client.get("/api/facets")
        second = self.client.get("/api/facets", headers={"If-None-Match": first.etag})
        self.assertEqual(second.status_code, 304)
        self.assertEqual(second.body, b"")

    def test_stale_etag_gets_a_full_response(self):
        res = self.client.get("/api/facets", headers={"If-None-Match": '"not-the-right-tag"'})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.body)

    def test_etag_changes_when_the_payload_changes(self):
        a = self.client.get("/api/documents?limit=1")
        b = self.client.get("/api/documents?limit=2")
        self.assertNotEqual(a.etag, b.etag)

    def test_head_returns_headers_without_a_body(self):
        res = self.client.request("/api/health", method="HEAD")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.body, b"")
        self.assertTrue(int(res.headers["content-length"]) > 0)

    def test_post_is_rejected_with_an_allow_header(self):
        res = self.client.request("/api/health", method="POST")
        self.assertEqual(res.status_code, 405)
        self.assertIn("GET", res.headers["allow"])

    def test_unknown_api_route_is_404_json(self):
        res = self.client.get("/api/nope")
        self.assertEqual(res.status_code, 404)
        self.assertIn("error", res.json())

    def test_parts_listing(self):
        body = self.client.get("/api/parts").json()
        self.assertEqual(len(body["parts"]), 8)
        numbers = [p["part_number"] for p in body["parts"]]
        self.assertEqual(numbers, sorted(numbers))

    def test_part_detail_joins_documents_and_datasets(self):
        body = self.client.get("/api/parts/QPF4216").json()
        self.assertEqual(body["part"]["part_number"], "QPF4216")
        self.assertEqual(len(body["documents"]), 5)
        self.assertEqual(len(body["datasets"]), 2)
        self.assertIn("summary", body["datasets"][0])

    def test_unknown_part_is_404(self):
        self.assertEqual(self.client.get("/api/parts/NOPE123").status_code, 404)

    def test_documents_pagination_envelope(self):
        body = self.client.get("/api/documents?limit=5&offset=5").json()
        self.assertEqual(body["total"], 40)
        self.assertEqual(body["limit"], 5)
        self.assertEqual(body["offset"], 5)
        self.assertEqual(len(body["documents"]), 5)

    def test_documents_filter(self):
        body = self.client.get("/api/documents?kind=errata&limit=50").json()
        self.assertTrue(body["documents"])
        self.assertTrue(all(d["kind"] == "errata" for d in body["documents"]))

    def test_bad_limit_is_400_naming_the_parameter(self):
        res = self.client.get("/api/documents?limit=abc")
        self.assertEqual(res.status_code, 400)
        self.assertIn("limit", res.json()["error"])

    def test_limit_above_the_cap_is_400(self):
        self.assertEqual(self.client.get("/api/documents?limit=9999").status_code, 400)

    def test_negative_offset_is_400(self):
        self.assertEqual(self.client.get("/api/documents?offset=-1").status_code, 400)

    def test_bad_order_column_is_400_not_500(self):
        res = self.client.get("/api/documents?order=body")
        self.assertEqual(res.status_code, 400)

    def test_document_detail_includes_tags(self):
        listing = self.client.get("/api/documents?limit=1").json()
        doc_id = listing["documents"][0]["id"]
        body = self.client.get("/api/documents/%d" % doc_id).json()
        self.assertIn("body", body)
        self.assertIn("part_number", body)
        self.assertIsInstance(body["tags"], list)

    def test_missing_document_is_404(self):
        self.assertEqual(self.client.get("/api/documents/999999").status_code, 404)

    def test_search_requires_a_query(self):
        res = self.client.get("/api/search")
        self.assertEqual(res.status_code, 400)
        self.assertIn("q", res.json()["error"])

    def test_search_returns_ranked_results(self):
        body = self.client.get("/api/search?q=QPF4216").json()
        self.assertGreater(body["total"], 0)
        self.assertEqual(body["results"][0]["part_number"], "QPF4216")
        self.assertIn("snippet", body["results"][0])

    def test_search_encodes_spaces(self):
        body = self.client.get("/api/search?q=return+loss").json()
        self.assertEqual(body["query"], "return loss")
        self.assertGreater(body["total"], 0)

    def test_dataset_detail_has_a_summary(self):
        part = self.client.get("/api/parts/QPA9903").json()
        ds = part["datasets"][0]
        body = self.client.get("/api/datasets/%d" % ds["id"]).json()
        self.assertEqual(body["ports"], 2)
        self.assertIn("max_gain_db", body["summary"])

    def test_trace_defaults_to_s21_in_db(self):
        part = self.client.get("/api/parts/QPA9903").json()
        ds_id = part["datasets"][0]["id"]
        body = self.client.get("/api/datasets/%d/trace" % ds_id).json()
        self.assertEqual(body["parameter"], "S21")
        self.assertEqual(body["kind"], "db")
        self.assertEqual(len(body["frequencies_hz"]), len(body["values"]))
        self.assertGreater(len(body["values"]), 100)

    def test_trace_honours_explicit_parameters(self):
        part = self.client.get("/api/parts/QPA9903").json()
        ds_id = part["datasets"][0]["id"]
        body = self.client.get("/api/datasets/%d/trace?i=1&j=1&kind=vswr" % ds_id).json()
        self.assertEqual(body["parameter"], "S11")
        self.assertTrue(all(v >= 1.0 for v in body["values"]))

    def test_trace_group_delay_is_available(self):
        part = self.client.get("/api/parts/QPF4216").json()
        ds_id = part["datasets"][0]["id"]
        body = self.client.get("/api/datasets/%d/trace?kind=group_delay" % ds_id).json()
        self.assertEqual(len(body["values"]), len(body["frequencies_hz"]))

    def test_trace_rejects_an_unknown_kind(self):
        part = self.client.get("/api/parts/QPA9903").json()
        ds_id = part["datasets"][0]["id"]
        res = self.client.get("/api/datasets/%d/trace?kind=smith" % ds_id)
        self.assertEqual(res.status_code, 400)

    def test_trace_rejects_a_port_index_the_file_does_not_have(self):
        part = self.client.get("/api/parts/TR50X").json()
        ds_id = part["datasets"][0]["id"]
        res = self.client.get("/api/datasets/%d/trace?i=2&j=1" % ds_id)
        self.assertEqual(res.status_code, 400)

    def test_missing_dataset_is_404(self):
        self.assertEqual(self.client.get("/api/datasets/999999").status_code, 404)

    def test_payload_is_json_serialisable_without_nan(self):
        part = self.client.get("/api/parts/TR50X").json()
        ds_id = part["datasets"][0]["id"]
        raw = self.client.get("/api/datasets/%d/trace?i=1&j=1&kind=vswr" % ds_id).body
        # allow_nan=False in the encoder means an infinity would have raised, so
        # reaching valid JSON here is the assertion.
        json.loads(raw.decode("utf-8"))


class StaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = support.Client(support.build_app())

    def test_root_serves_the_app_shell(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("text/html", res.headers["content-type"])
        self.assertIn(b"<title>", res.body)

    def test_javascript_is_served_with_the_right_type(self):
        res = self.client.get("/app.js")
        self.assertEqual(res.status_code, 200)
        self.assertIn("javascript", res.headers["content-type"])

    def test_missing_static_file_is_404(self):
        self.assertEqual(self.client.get("/nope.js").status_code, 404)

    def test_directory_traversal_is_refused(self):
        for attempt in ["/../server/api.py", "/..%2fserver/api.py", "/a/../../server/store.py"]:
            res = self.client.get(attempt)
            self.assertIn(res.status_code, (403, 404), attempt)
            self.assertNotIn(b"import", res.body, attempt)


if __name__ == "__main__":
    unittest.main()
