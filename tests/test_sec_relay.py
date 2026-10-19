"""The browser relay (tools/sec_relay.js) must give exactly the same SEC cache
entry as the direct Python path, for real and tricky inputs."""
import copy
import json
import shutil
import subprocess
import unittest
from pathlib import Path

import wamo_sec_relay as relay
import wamo_update_us_sec as us

ROOT = Path(__file__).resolve().parents[1]
EPS = ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic")
SALES = ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet")
STOCK = {"name": "Test Co", "industry": "Electrical Products", "sector": "Industrials", "krxSector": "산업재"}
SUBS = {"sic": "3690", "sicDescription": "MISC ELECTRICAL", "filings": {"recent": {
    "form": ["8-K"] + ["4"] * 80 + ["10-Q", "10-K"], "accessionNumber": ["0001-26-1"] + ["x"] * 80 + ["0001-26-2", "0001-25-9"],
    "primaryDocument": ["a.htm"] + ["f4.xml"] * 80 + ["q.htm", "k.htm"], "filingDate": ["2026-10-01"] + ["2026-09-01"] * 80 + ["2026-08-05", "2025-11-20"]}}}


def node_summary(facts, subs):
    script = ("const R=require(%s);const fs=require('fs');const x=JSON.parse(fs.readFileSync(0,'utf8'));"
              "process.stdout.write(JSON.stringify(R.summarize(x.facts,x.subs)));") % json.dumps(str(ROOT / "tools/sec_relay.js"))
    out = subprocess.run(["node", "-e", script], input=json.dumps({"facts": facts, "subs": subs}),
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def direct_parts(facts, sector):
    return {
        "eps": us._latest_quarter_yoy(facts, EPS), "sales": us._latest_quarter_yoy(facts, SALES),
        "annual": us._annual_metrics(facts, EPS),
        "epsHistory": us._quarter_growth_history(facts, EPS)[0],
        "salesHistory": us._quarter_growth_history(facts, SALES)[0],
        "grossMargin": us._margin_change(facts, ("GrossProfit",), SALES),
        "operatingMargin": us._margin_change(facts, ("OperatingIncomeLoss",), SALES),
        "health": us.financial_health_from_sec_facts(facts, sector),
    }


def rows(concept, unit, items, ns="us-gaap"):
    return {ns: {concept: {"units": {unit: items}}}}


def merge(*parts):
    out = {"facts": {}}
    for p in parts:
        for ns, concepts in p.items():
            out["facts"].setdefault(ns, {}).update(concepts)
    return out


def q(val, fy, fp, frame, end, filed, form="10-Q", accn="a"):
    r = {"val": val, "fy": fy, "fp": fp, "form": form, "end": end, "filed": filed, "accn": accn}
    if frame:
        r["frame"] = frame
    return r


def tricky():
    eps = [q(1.0, 2023, "Q2", "CY2023Q2", "2023-06-30", "2023-08-01"), q(1.2, 2024, "Q2", "CY2024Q2", "2024-06-30", "2024-08-01"),
           q(1.25, 2024, "Q2", "CY2024Q2", "2024-06-30", "2024-08-01", accn="b"),  # tie on end+filed: later accn wins
           q(-0.2, 2024, "Q3", "CY2024Q3", "2024-09-30", "2024-11-01"), q(0.4, 2025, "Q3", "CY2025Q3", "2025-09-30", "2025-11-01"),
           q(2.0, 2021, "FY", None, "2021-12-31", "2022-02-01", form="10-K"), q(2.5, 2022, "FY", None, "2022-12-31", "2023-02-01", form="10-K"),
           q(3.0, 2023, "FY", None, "2023-12-31", "2024-02-01", form="10-K"), q(3.3, 2024, "FY", None, "2024-12-31", "2025-02-01", form="10-K"),
           q(None, 2025, "Q1", "CY2025Q1", "2025-03-31", "2025-05-01"), q("n/a", 2025, "Q2", "CY2025Q2", "2025-06-30", "2025-08-01")]
    sales = [q(100 + i * 7, 2023 + (i // 4), f"Q{i % 4 + 1}" if i % 4 < 3 else "FY",
               f"CY{2023 + i // 4}Q{i % 4 + 1}", f"{2023 + i // 4}-{3 * (i % 4) + 3:02d}-28", f"{2023 + i // 4}-{3 * (i % 4) + 4:02d}-15")
             for i in range(11)]
    gp = [dict(r, val=r["val"] * 0.3 + i) for i, r in enumerate(sales)]
    oi = [dict(r, val=r["val"] * 0.1 - 5) for r in sales]
    bs = merge(rows("Assets", "USD", [q(500, 2025, "Q3", None, "2025-09-30", "2025-11-01"), q(520, 2025, "Q3", None, "2025-09-30", "2025-11-01")]),
               rows("StockholdersEquity", "USD", [q(200, 2025, "Q3", None, "2025-09-30", "2025-11-01")]),
               rows("AssetsCurrent", "USD", [q(150, 2025, "Q3", None, "2025-09-30", "2025-11-01")]),
               rows("LiabilitiesCurrent", "USD", [q(100, 2025, "Q3", None, "2025-09-30", "2025-11-01")]),
               rows("ProfitLoss", "USD", [q(30, 2024, "FY", None, "2024-12-31", "2025-02-01", form="10-K")]))
    return merge(rows("EarningsPerShareDiluted", "USD/shares", eps), rows("Revenues", "USD", sales),
                 rows("GrossProfit", "USD", gp), rows("OperatingIncomeLoss", "USD", oi), bs["facts"])


def ifrs_only():
    return merge(rows("EarningsPerShareBasic", "USD/shares", [q(1.0, 2024, "FY", None, "2024-12-31", "2025-03-01", form="20-F"),
                                                               q(1.5, 2025, "FY", None, "2025-12-31", "2026-03-01", form="20-F")], ns="ifrs-full"),
                 rows("Revenues", "USD", [q(10, 2024, "FY", None, "2024-12-31", "2025-03-01", form="20-F")], ns="ifrs-full"))


@unittest.skipUnless(shutil.which("node"), "node not installed")
class RelayParityTests(unittest.TestCase):
    def assert_same(self, facts):
        sector = STOCK["sector"]
        direct = us.build_sec_entry(STOCK, "0000000001", SUBS["sic"], SUBS["sicDescription"],
                                    [{"form": f, "accession": a, "doc": d, "date": t} for f, a, d, t in zip(*SUBS["filings"]["recent"].values())],
                                    direct_parts(facts, sector))
        sm = node_summary(facts, SUBS)
        via = us.build_sec_entry(STOCK, "0000000001", sm["sic"], sm["sicDescription"], sm["filings"],
                                 relay.parts_from_summary(sm, sector))
        self.assertEqual(json.dumps(direct, sort_keys=True), json.dumps(via, sort_keys=True))
        return direct

    def test_real_company_facts(self):
        facts = json.loads((ROOT / "tests/fixtures/sec_facts_flnc.json").read_text())
        entry = self.assert_same(facts)
        self.assertIsNotNone(entry["financialHealth"]["assets"])
        self.assertTrue(entry["secFilingUrl"].endswith("/1/0001259/k.htm"))  # 10-K found past 80 Form 4s

    def test_ties_missing_values_turnaround_and_margins(self):
        entry = self.assert_same(tricky())
        self.assertIsNotNone(entry["grossMargin"])
        self.assertEqual(entry["annualEpsSeries"][-1]["value"], 3.3)

    def test_ifrs_only_and_empty(self):
        self.assert_same(ifrs_only())
        self.assert_same({"facts": {}})

    def test_shuffled_order_still_matches(self):
        facts = tricky()
        for ns in facts["facts"].values():
            for c in ns.values():
                for u in c["units"]:
                    c["units"][u] = list(reversed(c["units"][u]))
        self.assert_same(facts)


class RelayApplyTests(unittest.TestCase):
    def test_apply_writes_live_cache_and_reports_errors(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy(ROOT / "us.html", root / "us.html")
            (root / "wamo_us_sec_cache.json").write_text(json.dumps({"TSLA": {"profileStatus": "LIVE", "secStatus": "NASDAQ_FALLBACK"}}))
            sm = {"cik": "0001868941", "sic": "3690", "sicDescription": "X", "filings": [],
                  "epsYoy": {"a": None, "b": None, "mode": None, "concept": None},
                  "salesYoy": {"a": 12.0, "b": 10.0, "mode": "YOY", "concept": "Revenues"},
                  "annual": {"years": [], "count": 0, "concept": None},
                  "epsHistory": [[], None], "salesHistory": [[{"frame": "CY2026Q2", "growthPct": 20.04}], "Revenues"],
                  "grossMargin": None, "operatingMargin": None, "health": {"equity": 5, "netIncome": 1, "assets": 9}}
            ok, bad = relay.apply({"TSLA": sm, "NOPE": {"error": "NO_CIK"}}, root=root)
            cache = json.loads((root / "wamo_us_sec_cache.json").read_text())
            self.assertEqual(ok, ["TSLA"])
            self.assertIn("NOPE", bad)
            self.assertEqual(cache["TSLA"]["secStatus"], "LIVE")
            self.assertEqual(cache["TSLA"]["sales_yoy"], 20.0)
            self.assertEqual(cache["TSLA"]["profileStatus"], "LIVE")  # Nasdaq profile kept
            self.assertEqual(cache["TSLA"]["quarterlySalesGrowthHistory"][0]["growthPct"], 20.0)


if __name__ == "__main__":
    unittest.main()
