"""SEC 차단 응답을 받으면 그 회차는 더 두드리지 않고 쉬는 시간을 기록하는지."""
import io
import unittest
import urllib.error
from unittest import mock

import wamo_update_us_sec as us


class SecGuardTests(unittest.TestCase):
    def setUp(self):
        us._SEC_BLOCKED = None

    def tearDown(self):
        us._SEC_BLOCKED = None

    def test_block_response_stops_further_requests(self):
        calls = []

        def fake(req, timeout=0):
            calls.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, io.BytesIO(b"Request Rate Threshold Exceeded"))

        with mock.patch.object(us, "SEC_MIN_INTERVAL", 0), mock.patch.object(us.urllib.request, "urlopen", fake):
            for path in ("a", "b", "c"):
                with self.assertRaises(us.SecBlocked):
                    us._sec_get("https://data.sec.gov/" + path)
        self.assertEqual(len(calls), 1)

    def test_guard_is_recorded_and_read_back(self):
        cache = {}
        us._sec_set_guard(cache, "HTTP 403")
        until = us._sec_guard_until(cache)
        self.assertIsNotNone(until)
        self.assertGreater(until, us.datetime.now(us.timezone.utc))

    def test_recent_company_map_is_reused_without_request(self):
        rows = {f"T{i}": {"cik": str(i).zfill(10), "title": "x"} for i in range(5001)}
        cache = {"__company_map__": {"fetchedAt": us.date.today().isoformat(), "rows": rows}}
        with mock.patch.object(us, "_sec_get", side_effect=AssertionError("요청하면 안 됨")):
            self.assertEqual(us.sec_company_map(cache), rows)


if __name__ == "__main__":
    unittest.main()
