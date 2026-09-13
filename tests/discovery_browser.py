"""Deterministic browser regression for discovery using the checked-in public snapshots."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
from urllib.parse import quote, urlparse

from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
CATALOG = json.loads((ROOT / "wamo_catalog.json").read_text(encoding="utf-8"))
RADAR = json.loads((ROOT / "wamo_radar.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 9, 13, 0, 0, tzinfo=timezone.utc)
TTL_MS = 3 * 60 * 60 * 1000


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass


class SourcePlan:
    """Return controlled source results while keeping rendering/model/store real."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = 0

    def serve(self, route):
        index = min(self.calls, len(self.steps) - 1)
        self.calls += 1
        result = self.steps[index]
        if result == "network-error":
            route.abort("failed")
        else:
            route.fulfill(json=result)


def fresh_radar():
    result = deepcopy(RADAR)
    result["checkedAt"] = NOW.isoformat().replace("+00:00", "Z")
    return result


def all_pass_signals(radar):
    return [
        signal
        for edition in radar["editions"].values()
        if edition.get("status") == "PASS"
        for signal in edition.get("signals", [])
    ]


def identity_for_signal(signal):
    return f"{signal['market'].split('-', 1)[0]}:{str(signal['symbol']).upper()}"


def public_counts(catalog, radar):
    catalog_ids = {row["id"] for row in catalog["stocks"]}
    signal_ids = {identity_for_signal(row) for row in all_pass_signals(radar)}
    return len(catalog_ids), len(signal_ids), len(catalog_ids | signal_ids)


def result_total(page):
    match = re.match(r"([\d,]+)개 ", page.locator("#discovery-count").inner_text())
    assert match, page.locator("#discovery-count").inner_text()
    return int(match.group(1).replace(",", ""))


def route_sources(page, base_url, catalog_plan, radar_plan):
    def handler(route):
        url = route.request.url
        path = urlparse(url).path
        if path.endswith("/wamo_catalog.json"):
            catalog_plan.serve(route)
        elif path.endswith("/wamo_radar.json"):
            radar_plan.serve(route)
        elif url.startswith(base_url):
            route.continue_()
        else:
            route.abort("blockedbyclient")

    page.route("**/*", handler)


def open_page(browser, base_url, *, catalog_steps=(CATALOG,), radar_steps=None,
              width=1440, path="/discovery.html"):
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    page.clock.install(time=NOW)
    page.set_default_timeout(10_000)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    catalog_plan = SourcePlan(*catalog_steps)
    radar_plan = SourcePlan(*(radar_steps or (fresh_radar(),)))
    route_sources(page, base_url, catalog_plan, radar_plan)
    page.goto(base_url + path, wait_until="domcontentloaded", timeout=60_000)
    page.locator("#discovery-query" if path == "/discovery.html" else "#wamo-live-status").wait_for()
    return context, page, errors, catalog_plan, radar_plan


def wait_status(page, *parts):
    status = page.locator("#discovery-status")
    for part in parts:
        expect(status).to_contain_text(part)
    expect(page.locator("#discovery-retry")).to_be_enabled()


def assert_no_overflow(page):
    dimensions = page.evaluate("({scroll:document.documentElement.scrollWidth, client:document.documentElement.clientWidth})")
    assert dimensions["scroll"] <= dimensions["client"], dimensions


def test_dedicated_navigation(browser, base_url):
    for width in (1440, 521, 540, 390):
        for name in ("index.html", "us.html", "movers.html"):
            context = browser.new_context(viewport={"width": width, "height": 900})
            page = context.new_page()
            page.clock.install(time=NOW)
            catalog_plan, radar_plan = SourcePlan(CATALOG), SourcePlan(fresh_radar())
            route_sources(page, base_url, catalog_plan, radar_plan)
            try:
                page.goto(f"{base_url}/{name}", wait_until="networkidle")
                link = page.get_by_role("link", name="🌐 글로벌 종목 발견", exact=True)
                expect(link).to_be_visible()
                assert page.locator('.market-switch').evaluate_all("""navs => navs.every(nav => {
                    const box = nav.getBoundingClientRect();
                    return [...nav.children].every(child => {
                        const rect = child.getBoundingClientRect();
                        return rect.left >= box.left && rect.right <= box.right;
                    });
                })"""), f"navigation content overflow at {width}px on {name}"
                assert page.locator("#wamo-discovery").count() == 0
                assert catalog_plan.calls == radar_plan.calls == 0
                link.click()
                page.wait_for_url("**/discovery.html")
                wait_status(page, "레이더: 연결 확인 유효", "카탈로그: 연결됨")
                assert result_total(page) > 0
                expect(page.locator('a[aria-current="page"]')).to_have_text("🌐 글로벌 종목 발견")
                assert page.evaluate("typeof window.WAMO_DATA") == "undefined"
                assert_no_overflow(page)
                page.locator(f'nav a[href="{name}"]').click()
                page.wait_for_url(f"**/{name}")
                expect(page.locator("#wamo-live-status")).to_be_visible()
                assert page.locator("#wamo-discovery").count() == 0
                print("DISCOVERY NAVIGATION PASS", name, width)
            finally:
                context.close()


def test_entry_errors(browser, base_url):
    context = browser.new_context()
    page = context.new_page()
    route_sources(page, base_url, SourcePlan(CATALOG), SourcePlan(fresh_radar()))
    try:
        page.route("**/discovery/view.js", lambda route: route.abort("failed"))
        page.goto(base_url + "/discovery.html")
        expect(page.locator("#discovery-load-error")).to_be_visible()
        page.get_by_role("link", name="🇰🇷 한국", exact=True).click()
        expect(page.locator("#wamo-live-status")).to_be_visible()
        page.goto(base_url + "/index.html?stock=__missing__")
        expect(page.locator("#discovery-original-notice")).to_be_visible()
        assert page.locator("#wamo-discovery").count() == 0
        assert page.locator('#drawer[aria-hidden="false"]').count() == 0
        print("DISCOVERY ENTRY ERRORS PASS")
    finally:
        context.close()


def test_real_snapshot_interactions(browser, base_url, width):
    radar = fresh_radar()
    context, page, errors, _, _ = open_page(browser, base_url, radar_steps=(radar,), width=width)
    try:
        wait_status(page, "레이더: 연결 확인 유효", "카탈로그: 연결됨")
        catalog_count, radar_count, union_count = public_counts(CATALOG, radar)
        scope = page.locator("#discovery-scope").inner_text()
        assert f"{catalog_count:,}종목" in scope
        assert f"{radar_count}종목" in scope
        assert f"중복 제외 {union_count:,}종목" in scope
        assert page.locator(".discovery-stock").count() == min(24, radar_count)
        for label in ["발견 방식", "국가", "레이더 산업 경로", "신고가 유형", "WAMO 기술 조건", "종목 정렬"]:
            assert page.get_by_label(label, exact=True).count() == 1

        query = page.locator("#discovery-query")
        query.fill("미국")
        us_expected = len({identity_for_signal(row) for row in all_pass_signals(radar) if row["market"].startswith("US-")})
        assert result_total(page) == us_expected
        assert set(page.locator(".discovery-stock").evaluate_all("rows => rows.map(row => row.dataset.country)")) == {"US"}

        query.fill("")
        page.locator("#discovery-country").select_option("KR")
        page.locator("#discovery-mode").select_option("industries")
        assert page.locator(".discovery-industry").count() > 0
        assert set(page.locator(".discovery-industry").evaluate_all("rows => rows.map(row => row.dataset.country)")) == {"KR"}

        industry = page.locator(".discovery-industry").first
        industry_key = industry.get_attribute("data-industry")
        industry_path = json.loads(industry_key)
        expected_names = sorted({
            row["name"] for row in all_pass_signals(radar)
            if row["market"].startswith("KR-") and row.get("industry_path") == industry_path
        })
        industry.locator("button").click()
        assert page.locator("#discovery-mode").input_value() == "candidates"
        assert page.locator("#discovery-country").input_value() == "KR"
        assert page.locator("#discovery-industry").input_value() == industry_key
        assert set(page.locator(".discovery-stock").evaluate_all("rows => rows.map(row => row.dataset.industry)")) == {industry_key}
        visible_text = page.locator(".discovery-stock button").all_inner_texts()
        assert visible_text and all(any(name in text for name in expected_names) for text in visible_text)

        page.locator("#discovery-reset").click()
        page.locator("#discovery-signal").select_option("close_252")
        page.locator("#discovery-technical").select_option("stage2")
        catalog_by_id = {row["id"]: row for row in CATALOG["stocks"]}
        expected_combined = 0
        for signal in {identity_for_signal(row): row for row in all_pass_signals(radar)}.values():
            catalog_row = catalog_by_id.get(identity_for_signal(signal))
            technical = (catalog_row or {}).get("technical") or {}
            as_of = signal["as_of"]
            normalized_as_of = f"{as_of[:4]}-{as_of[4:6]}-{as_of[6:]}"
            if (signal["flags"].get("close_252") is True and technical.get("ready") is True
                    and technical.get("asOf") == normalized_as_of and technical.get("stage2") is True
                    and isinstance(technical.get("aligned"), bool)
                    and isinstance(technical.get("rsPercentile"), (int, float))):
                expected_combined += 1
        assert result_total(page) == expected_combined
        assert all("종가 52주" in text for text in page.locator(".discovery-stock").all_inner_texts())
        page.locator("#discovery-reset").click()
        assert page.locator("#discovery-signal").input_value() == ""
        assert page.locator("#discovery-technical").input_value() == ""

        page.locator("#discovery-mode").select_option("search")
        query.fill("현대해상")
        expect(page.locator('.discovery-stock[data-id="KR:001450"]')).to_have_count(1)
        assert "현재 공개 후보 목록에 없음" in page.locator('.discovery-stock[data-id="KR:001450"]').inner_text()
        trigger = page.locator('.discovery-stock[data-id="KR:001450"] button')
        trigger.click()
        detail = page.locator("#discovery-detail")
        expect(detail).to_be_visible()
        assert detail.locator(".discovery-original-link").get_attribute("href") == "index.html?stock=001450.KS"
        detail.get_by_role("button", name="닫기", exact=True).click()
        expect(detail).to_have_count(0)
        assert page.evaluate('document.activeElement.closest(".discovery-stock")?.dataset.id') == "KR:001450"

        trigger.click()
        page.clock.fast_forward(30_001)
        page.keyboard.press("Escape")
        expect(page.locator("#discovery-detail")).to_have_count(0)
        assert page.evaluate('document.activeElement.closest(".discovery-stock")?.dataset.id') == "KR:001450"

        radar_ids = {identity_for_signal(row) for row in all_pass_signals(radar)}
        us_non_candidate = next(row for row in CATALOG["stocks"] if row["country"] == "US" and row["id"] not in radar_ids)
        query.fill(f"  {us_non_candidate['symbol'].lower()}  ")
        expect(page.locator(f'.discovery-stock[data-id="{us_non_candidate["id"]}"]')).to_have_count(1)

        query.fill("__outside_published_scope__")
        assert result_total(page) == 0
        assert "현재 제공 범위에서 찾을 수 없음" in page.locator("#discovery-results").inner_text()
        assert "연결 실패" not in page.locator("#discovery-status").inner_text()

        page.locator("#discovery-reset").click()
        focused = page.locator(".discovery-stock").first
        focused.locator("button").focus()
        focused_id = focused.get_attribute("data-id")
        page.clock.fast_forward(30_001)
        assert page.evaluate('document.activeElement.closest(".discovery-stock")?.dataset.id') == focused_id
        assert_no_overflow(page)
        assert not errors, errors
        print("DISCOVERY SNAPSHOT PASS", width)
    finally:
        context.close()


def test_stale_and_expiry(browser, base_url):
    stale = fresh_radar()
    stale["checkedAt"] = (NOW - timedelta(milliseconds=TTL_MS + 1)).isoformat().replace("+00:00", "Z")
    context, page, errors, _, _ = open_page(browser, base_url, radar_steps=(stale,))
    try:
        wait_status(page, "연결 확인 지연 · 과거 자료")
        assert result_total(page) == 0
        assert page.locator("#discovery-query").is_enabled()
        page.locator("#discovery-historical").check()
        assert result_total(page) > 0
        assert "과거 확인 신호" in page.locator(".discovery-stock").first.inner_text()
        assert not errors, errors
    finally:
        context.close()

    fresh = fresh_radar()
    context, page, errors, _, _ = open_page(browser, base_url, radar_steps=(fresh,))
    try:
        wait_status(page, "연결 확인 유효")
        assert result_total(page) > 0
        page.clock.fast_forward(TTL_MS + 2)
        expect(page.locator("#discovery-status")).to_contain_text("연결 확인 지연 · 과거 자료")
        assert result_total(page) == 0
        assert page.locator("#discovery-query").is_enabled()
        page.locator("#discovery-historical").check()
        assert result_total(page) > 0
        assert "과거" in page.locator("#discovery-count").inner_text()
        assert not errors, errors
        print("DISCOVERY STALE/EXPIRY PASS")
    finally:
        context.close()


def test_dialog_focus_across_source_refresh(browser, base_url):
    fresh = fresh_radar()
    signal = all_pass_signals(fresh)[0]
    row_id = identity_for_signal(signal)
    updated = deepcopy(fresh)
    updated["checkedAt"] = (NOW + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    updated_signal = next(row for row in all_pass_signals(updated) if identity_for_signal(row) == row_id)
    updated_signal["name"] += " · 갱신본"
    context, page, errors, _, _ = open_page(browser, base_url, radar_steps=(fresh, updated))
    try:
        wait_status(page, "레이더: 연결 확인 유효")
        page.locator("#discovery-mode").select_option("search")
        page.locator("#discovery-query").fill(signal["symbol"])
        card = page.locator(f'.discovery-stock[data-id="{row_id}"]')
        card.locator("button").evaluate("node => window.__discoveryOrigin = node")
        card.locator("button").click()
        page.clock.fast_forward(5 * 60 * 1000 + 1)
        wait_status(page, "레이더: 연결 확인 유효")
        expect(page.locator("#discovery-detail")).to_be_visible()
        expect(card).to_contain_text("갱신본")
        assert page.evaluate("!window.__discoveryOrigin.isConnected")
        page.keyboard.press("Escape")
        assert page.evaluate('document.activeElement.closest(".discovery-stock")?.dataset.id') == row_id
        assert not errors, errors
    finally:
        context.close()

    context, page, errors, _, _ = open_page(
        browser, base_url, radar_steps=(fresh, "network-error")
    )
    try:
        wait_status(page, "레이더: 연결 확인 유효")
        card = page.locator(".discovery-stock").first
        card.locator("button").evaluate("node => window.__discoveryOrigin = node")
        card.locator("button").click()
        page.clock.fast_forward(5 * 60 * 1000 + 1)
        wait_status(page, "레이더: 연결 실패")
        expect(page.locator(".discovery-stock")).to_have_count(0)
        expect(page.locator("#discovery-detail")).to_be_visible()
        assert page.evaluate("!window.__discoveryOrigin.isConnected")
        page.locator("#discovery-detail-close").click()
        assert page.evaluate("document.activeElement.id") == "discovery-count"
        assert not errors, errors
        print("DISCOVERY DIALOG REFRESH FOCUS PASS")
    finally:
        context.close()


def test_failed_refresh_and_recovery(browser, base_url):
    fresh = fresh_radar()
    context, page, errors, catalog_plan, radar_plan = open_page(
        browser, base_url,
        catalog_steps=(CATALOG, "network-error", CATALOG),
        radar_steps=(fresh, "network-error", fresh),
    )
    try:
        wait_status(page, "연결 확인 유효", "카탈로그: 연결됨")
        initial = result_total(page)
        page.locator("#discovery-retry").click()
        wait_status(page, "레이더: 연결 실패", "카탈로그: 연결 실패")
        assert result_total(page) == 0
        assert page.locator("#discovery-query").is_enabled()
        page.locator("#discovery-historical").check()
        assert result_total(page) == initial
        assert "과거 확인 신호" in page.locator(".discovery-stock").first.inner_text()
        page.locator("#discovery-retry").click()
        wait_status(page, "레이더: 연결 확인 유효", "카탈로그: 연결됨")
        assert result_total(page) == initial
        assert catalog_plan.calls >= 3 and radar_plan.calls >= 3
        assert not errors, errors
        print("DISCOVERY RETRY RECOVERY PASS")
    finally:
        context.close()


def test_independent_and_invalid_sources(browser, base_url):
    fresh = fresh_radar()
    cases = [
        (("network-error",), (fresh,), ("레이더: 연결 확인 유효", "카탈로그: 연결 실패"), True),
        ((CATALOG,), ("network-error",), ("레이더: 연결 실패", "카탈로그: 연결됨"), False),
        (("network-error",), ("network-error",), ("레이더: 연결 실패", "카탈로그: 연결 실패"), False),
    ]
    for catalog_steps, radar_steps, labels, candidates_visible in cases:
        context, page, errors, _, _ = open_page(browser, base_url, catalog_steps=catalog_steps, radar_steps=radar_steps)
        try:
            wait_status(page, *labels)
            assert (result_total(page) > 0) is candidates_visible
            assert page.locator("#discovery-query").is_enabled()
            if labels[0] == "레이더: 연결 실패" and labels[1] == "카탈로그: 연결됨":
                page.locator("#discovery-mode").select_option("search")
                page.locator("#discovery-query").fill("현대해상")
                expect(page.locator('.discovery-stock[data-id="KR:001450"]')).to_have_count(1)
            assert not errors, errors
        finally:
            context.close()

    invalid_catalog = {"schemaVersion": 1, "stocks": "not-an-array"}
    invalid_radar = {"schemaVersion": 1, "editions": {}}
    context, page, errors, _, _ = open_page(
        browser, base_url, catalog_steps=(invalid_catalog,), radar_steps=(invalid_radar,)
    )
    try:
        wait_status(page, "레이더: 자료 형식 오류", "카탈로그: 자료 형식 오류")
        assert "연결 실패" not in page.locator("#discovery-status").inner_text()
        assert result_total(page) == 0
        assert not errors, errors
    finally:
        context.close()

    malformed_industry = fresh_radar()
    asia = deepcopy(malformed_industry["editions"]["ASIA"])
    asia["industries"][0]["current_signal_issuers"] = asia["industries"][0]["eligible_classified_issuers"] + 1
    malformed_industry["editions"] = {"ASIA": asia}
    context, page, errors, _, _ = open_page(browser, base_url, radar_steps=(malformed_industry,))
    try:
        wait_status(page, "레이더: 자료 형식 오류")
        assert "ASIA 형식 오류" in page.locator("#discovery-editions").inner_text()
        assert result_total(page) == 0
        assert not errors, errors
    finally:
        context.close()

    partial = fresh_radar()
    partial["editions"]["US"] = {**partial["editions"]["US"], "status": "FAILED"}
    context, page, errors, _, _ = open_page(browser, base_url, radar_steps=(partial,))
    try:
        wait_status(page, "레이더: 연결 확인 유효")
        expected_asia = len({identity_for_signal(row) for row in partial["editions"]["ASIA"]["signals"]})
        assert result_total(page) == expected_asia
        assert "미국 갱신 실패" in page.locator("#discovery-editions").inner_text()
        visible_countries = set(page.locator(".discovery-stock").evaluate_all("rows => rows.map(row => row.dataset.country)"))
        assert "US" not in visible_countries
        assert visible_countries
        assert not errors, errors
        print("DISCOVERY SOURCE STATES PASS")
    finally:
        context.close()

    all_failed = fresh_radar()
    all_failed["editions"] = {
        name: {**edition, "status": "FAILED"}
        for name, edition in all_failed["editions"].items()
    }
    context, page, errors, _, _ = open_page(browser, base_url, radar_steps=(all_failed,))
    try:
        wait_status(page, "레이더: 연결 확인 유효")
        editions = page.locator("#discovery-editions").inner_text()
        assert "미국 갱신 실패" in editions and "ASIA 갱신 실패" in editions
        assert result_total(page) == 0
        assert "갱신 실패" in page.locator("#discovery-results").inner_text()
        assert not errors, errors
    finally:
        context.close()


def test_catalog_revalidation_detail(browser, base_url):
    fresh = fresh_radar()
    catalog_by_id = {row["id"]: row for row in CATALOG["stocks"]}
    signal = next(
        row for row in all_pass_signals(fresh)
        if identity_for_signal(row) in catalog_by_id
        and catalog_by_id[identity_for_signal(row)].get("technical", {}).get("ready") is True
        and catalog_by_id[identity_for_signal(row)]["technical"].get("asOf")
            == f"{row['as_of'][:4]}-{row['as_of'][4:6]}-{row['as_of'][6:]}"
    )
    row_id = identity_for_signal(signal)
    context, page, errors, _, _ = open_page(
        browser, base_url,
        catalog_steps=(CATALOG, "network-error"), radar_steps=(fresh, fresh),
    )
    try:
        wait_status(page, "레이더: 연결 확인 유효", "카탈로그: 연결됨")
        page.locator("#discovery-retry").click()
        wait_status(page, "레이더: 연결 확인 유효", "카탈로그: 연결 실패")
        assert result_total(page) > 0
        page.locator("#discovery-mode").select_option("search")
        page.locator("#discovery-query").fill(signal["symbol"])
        card = page.locator(f'.discovery-stock[data-id="{row_id}"]')
        expect(card).to_have_count(1)
        card.locator("button").click()
        detail_text = page.locator("#discovery-detail").inner_text()
        assert "카탈로그 연결 실패" in detail_text
        assert "과거 신고가 관측" not in detail_text
        assert not errors, errors
        print("DISCOVERY CATALOG REVALIDATION PASS")
    finally:
        context.close()


def test_original_detail_without_radar(browser, base_url):
    for country, page_name in [("KR", "index.html"), ("US", "us.html")]:
        row = next(stock for stock in CATALOG["stocks"] if stock["country"] == country)
        path = f"/{page_name}?stock={quote(row['ticker'])}"
        context, page, errors, catalog_plan, radar_plan = open_page(
            browser, base_url, radar_steps=("network-error",), path=path
        )
        try:
            expect(page.locator('#drawer[aria-hidden="false"]')).to_be_visible()
            assert row["ticker"].split(".", 1)[0] in page.locator("#detailSub").inner_text()
            assert page.locator("#wamo-discovery").count() == 0
            assert catalog_plan.calls == radar_plan.calls == 0
            assert_no_overflow(page)
            assert not errors, errors
            print("DISCOVERY ORIGINAL DETAIL PASS", country)
        finally:
            context.close()


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(ROOT)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                test_dedicated_navigation(browser, base_url)
                test_entry_errors(browser, base_url)
                test_real_snapshot_interactions(browser, base_url, 1440)
                test_real_snapshot_interactions(browser, base_url, 390)
                test_stale_and_expiry(browser, base_url)
                test_dialog_focus_across_source_refresh(browser, base_url)
                test_failed_refresh_and_recovery(browser, base_url)
                test_independent_and_invalid_sources(browser, base_url)
                test_catalog_revalidation_detail(browser, base_url)
                test_original_detail_without_radar(browser, base_url)
            finally:
                browser.close()
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
