import copy
import math
import unittest

from wamo_catalog import build_catalog


CHECKS = {
    "주가>150·200일선": True,
    "150일선>200일선": True,
    "200일선 상승": True,
    "50일선>150·200일선": True,
    "주가>50일선": True,
    "52주 저점 대비 +30%": True,
    "52주 고점 25% 이내": True,
}


def kr_stock(**overrides):
    stock = {
        "ticker": "001450.KS",
        "stock_code": "001450",
        "name": "현대해상",
        "sector": "손해보험",
        "krx_market": "KOSPI",
        "date": "2026-09-11",
        "close": 51300.0,
        "dataSource": "Yahoo Finance",
        "technicalCoverage": "FULL",
        "historyReady200": True,
        "isStale": False,
        "stage2Core": True,
        "trendTemplate": True,
        "alignment": {"isAligned": True, "private_history": [1, 2, 3]},
        "rsPercentile": 95.5,
        "ma50": 43238.0,
        "ma150": 36384.67,
        "ma200": 34546.75,
        "stage2Checks": dict(CHECKS),
        "private_value": "must never be published",
        "history": [{"date": "2026-09-11", "close": 51300.0}],
    }
    stock.update(overrides)
    return stock


def us_stock(**overrides):
    stock = {
        "ticker": "NVDA",
        "stock_code": "NVDA",
        "name": "NVIDIA Corporation",
        "sector": "Technology",
        "krx_market": "NASDAQ",
        "date": "2026-09-11",
        "close": 190.17,
        "dataSource": "Yahoo Finance",
        "technicalCoverage": "FULL",
        "historyReady200": True,
        "isStale": False,
        "stage2Core": True,
        "trendTemplate": False,
        "alignment": {"isAligned": True},
        "rsPercentile": 89.2,
        "ma50": 180.0,
        "ma150": 160.0,
        "ma200": 150.0,
        "stage2Checks": dict(CHECKS),
    }
    stock.update(overrides)
    return stock


class CatalogTests(unittest.TestCase):
    def build(self, kr=None, us=None):
        payloads = {}
        if kr is not None:
            payloads["KR"] = {
                "meta": {"asOf": "2026-09-11", "source": "KR meta source"},
                "stocks": kr,
            }
        if us is not None:
            payloads["US"] = {
                "meta": {"asOf": "2026-09-11", "source": "US meta source"},
                "stocks": us,
            }
        return build_catalog(payloads, "2026-09-13T00:00:00Z")

    def test_projects_kr_identity_and_only_public_fields(self):
        source = {"KR": {"meta": {"asOf": "2026-09-11"}, "stocks": [kr_stock()]}}
        before = copy.deepcopy(source)

        out = build_catalog(source, "2026-09-13T00:00:00Z")

        self.assertEqual(source, before)
        self.assertEqual(out["schemaVersion"], 1)
        self.assertEqual(out["generatedAt"], "2026-09-13T00:00:00Z")
        self.assertEqual(out["markets"], {
            "KR": {"asOf": "2026-09-11", "count": 1},
            "US": {"asOf": None, "count": 0},
        })
        row = out["stocks"][0]
        self.assertEqual(set(row), {
            "id", "country", "market", "symbol", "ticker", "name", "sector",
            "asOf", "currency", "close", "detailUrl", "source", "technical",
        })
        self.assertEqual(row["id"], "KR:001450")
        self.assertEqual(row["country"], "KR")
        self.assertEqual(row["market"], "KR-KOSPI")
        self.assertEqual(row["symbol"], "001450")
        self.assertEqual(row["ticker"], "001450.KS")
        self.assertEqual(row["currency"], "KRW")
        self.assertEqual(row["detailUrl"], "index.html?stock=001450.KS")
        self.assertEqual(row["source"], "Yahoo Finance")
        self.assertNotIn("private_value", row)
        self.assertNotIn("history", row)

    def test_projects_us_identity_and_encodes_detail_ticker(self):
        row = self.build(us=[us_stock(ticker="BRK/B", stock_code="BRK/B")])["stocks"][0]

        self.assertEqual(row["id"], "US:BRK/B")
        self.assertEqual(row["country"], "US")
        self.assertEqual(row["market"], "US")
        self.assertEqual(row["symbol"], "BRK/B")
        self.assertEqual(row["ticker"], "BRK/B")
        self.assertEqual(row["currency"], "USD")
        self.assertEqual(row["detailUrl"], "us.html?stock=BRK%2FB")

    def test_preserves_six_character_alphanumeric_kr_issue_code(self):
        row = self.build(kr=[kr_stock(
            ticker="0126Z0.KS", stock_code="0126Z0", name="삼성에피스홀딩스",
        )])["stocks"][0]

        self.assertEqual(row["id"], "KR:0126Z0")
        self.assertEqual(row["symbol"], "0126Z0")

    def test_rejects_duplicate_country_symbol_identity(self):
        with self.assertRaisesRegex(ValueError, "duplicate.*KR:001450"):
            self.build(kr=[kr_stock(), kr_stock(ticker="001450.KQ", krx_market="KOSDAQ")])

    def test_same_symbol_in_different_countries_is_not_duplicate(self):
        out = self.build(
            kr=[kr_stock(ticker="001450.KS", stock_code="001450")],
            us=[us_stock(ticker="001450", stock_code="001450")],
        )

        self.assertEqual([row["id"] for row in out["stocks"]], ["KR:001450", "US:001450"])

    def test_rejects_invalid_market_or_stock_dates(self):
        cases = [
            ({"meta": {"asOf": "09/11/2026"}, "stocks": [kr_stock()]}, "market"),
            ({"meta": {"asOf": "2026-09-11"}, "stocks": [kr_stock(date="2026-02-30")]}, "stock"),
        ]
        for payload, label in cases:
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, "date"):
                build_catalog({"KR": payload}, "2026-09-13T00:00:00Z")

    def test_rejects_nonfinite_close(self):
        for value in (math.nan, math.inf, -math.inf, "NaN"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "close"):
                self.build(kr=[kr_stock(close=value)])

    def test_missing_technical_metadata_keeps_search_record_with_unknown_indicators(self):
        row = self.build(kr=[kr_stock(
            technicalCoverage=None,
            historyReady200=None,
            isStale=None,
            trendTemplate=None,
            alignment=None,
            rsPercentile=None,
            ma50=None,
            ma150=None,
            ma200=None,
            stage2Checks=None,
        )])["stocks"][0]

        self.assertEqual(row["technical"], {
            "ready": False,
            "asOf": "2026-09-11",
            "stage2": None,
            "aligned": None,
            "rsPercentile": None,
            "ma50": None,
            "ma150": None,
            "ma200": None,
            "checks": {},
        })

    def test_unready_or_invalid_technical_values_are_all_unknown(self):
        rows = self.build(kr=[
            kr_stock(ticker="001450.KS", stock_code="001450", historyReady200=False),
            kr_stock(ticker="005930.KS", stock_code="005930", isStale=True),
            kr_stock(ticker="000660.KS", stock_code="000660", technicalCoverage="SHORT_HISTORY"),
            kr_stock(ticker="035420.KS", stock_code="035420", rsPercentile=math.inf),
        ])["stocks"]

        expected = {
            "ready": False,
            "asOf": "2026-09-11",
            "stage2": None,
            "aligned": None,
            "rsPercentile": None,
            "ma50": None,
            "ma150": None,
            "ma200": None,
            "checks": {},
        }
        self.assertTrue(all(row["technical"] == expected for row in rows))

    def test_ready_technical_projection_allowlists_checks_and_boolean_values(self):
        supplied_checks = dict(CHECKS)
        supplied_checks["private vendor score"] = True
        supplied_checks["주가>50일선"] = "yes"
        row = self.build(kr=[kr_stock(stage2Checks=supplied_checks)])["stocks"][0]

        technical = row["technical"]
        self.assertTrue(technical["ready"])
        self.assertIs(technical["stage2"], True)
        self.assertIs(technical["aligned"], True)
        self.assertEqual(technical["rsPercentile"], 95.5)
        self.assertEqual(technical["ma50"], 43238.0)
        self.assertNotIn("private vendor score", technical["checks"])
        self.assertNotIn("주가>50일선", technical["checks"])
        self.assertEqual(len(technical["checks"]), 6)
        self.assertTrue(all(type(value) is bool for value in technical["checks"].values()))

    def test_stage2_is_core_result_not_rs_inclusive_trend_template(self):
        row = self.build(kr=[kr_stock(
            stage2Core=True, trendTemplate=False, rsPercentile=65.0,
        )])["stocks"][0]

        self.assertIs(row["technical"]["stage2"], True)


if __name__ == "__main__":
    unittest.main()
