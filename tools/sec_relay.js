// SEC 중계 계산기 — runs in the person's own browser (the Claude app's browser pane),
// because SEC blocks GitHub's shared servers. It downloads each company's SEC
// files there and returns only the few numbers the dashboard needs (~1 KB/company).
//
// The math is a line-by-line port of wamo_update_us_sec.py (_fact_units,
// _quarter_frames, _quarter_growth_history, _margin_change, _latest_quarter_yoy,
// _annual_metrics, _latest_fact_value). Numbers come back UNROUNDED; Python does
// the rounding and builds the cache entry, so both paths round the same way.
// tests/test_sec_relay.py runs this file in Node and compares with Python.
var SEC_RELAY = (function () {
  var EPS = ['EarningsPerShareDiluted', 'EarningsPerShareBasicAndDiluted', 'EarningsPerShareBasic'];
  var SALES = ['RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet'];
  var QFRAME = /^CY(\d{4})Q([1-4])$/;

  function num(v) {
    if (v === null || v === undefined) return null;
    var t = String(v).trim().replace(/\$/g, '').replace(/,/g, '').replace(/%/g, '');
    if (!t || ['n/a', 'na', 'none', 'null', '-'].indexOf(t.toLowerCase()) >= 0) return null;
    var mult = 1, last = t.slice(-1).toUpperCase();
    if ({ K: 1, M: 1, B: 1, T: 1 }[last]) { mult = { K: 1e3, M: 1e6, B: 1e9, T: 1e12 }[last]; t = t.slice(0, -1); }
    if (!/^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(t)) return null;
    var n = parseFloat(t) * mult;
    return isFinite(n) ? n : null;
  }
  var s = function (v) { return v === null || v === undefined ? '' : String(v); };
  // Python compares tuples of strings lexicographically.
  function rankGe(a, b) { for (var i = 0; i < a.length; i++) { if (a[i] > b[i]) return true; if (a[i] < b[i]) return false; } return true; }
  var rank = function (r) { return [s(r.end), s(r.filed), s(r.accn)]; };

  function factUnits(facts, concepts) {
    var root = (facts || {}).facts || {};
    var spaces = ['us-gaap', 'ifrs-full'];
    for (var i = 0; i < spaces.length; i++) {
      var tax = root[spaces[i]] || {};
      for (var j = 0; j < concepts.length; j++) {
        var units = (tax[concepts[j]] || {}).units || {};
        var order = ['USD/shares', 'USD', 'shares'];
        for (var k = 0; k < order.length; k++) if (units[order[k]] && units[order[k]].length) return [units[order[k]], concepts[j]];
      }
    }
    return [[], null];
  }

  function quarterFrames(facts, concepts) {
    var fu = factUnits(facts, concepts), values = {};
    fu[0].forEach(function (row) {
      var frame = s(row.frame);
      if (!QFRAME.test(frame) || (row.form !== '10-Q' && row.form !== '10-K')) return;
      var v = num(row.val); if (v === null) return;
      var prev = values[frame];
      if (!prev || s(row.filed) >= prev[0]) values[frame] = [s(row.filed), v];
    });
    var out = {}; Object.keys(values).forEach(function (f) { out[f] = values[f][1]; });
    return [out, fu[1]];
  }

  function growthHistory(facts, concepts, count) {
    count = count || 3;
    var qf = quarterFrames(facts, concepts), values = qf[0], result = [];
    Object.keys(values).sort().forEach(function (frame) {
      var m = QFRAME.exec(frame); if (!m) return;
      var prev = 'CY' + (parseInt(m[1], 10) - 1) + 'Q' + m[2];
      var cur = values[frame], old = values[prev];
      if (old === undefined || old === null || old <= 0) return;
      result.push({ frame: frame, growthPct: (cur / old - 1) * 100 });
    });
    return [result.slice(-count), qf[1]];
  }

  function marginChange(facts, numerConcepts, revenueConcepts) {
    var n = quarterFrames(facts, numerConcepts), r = quarterFrames(facts, revenueConcepts);
    var num_ = n[0], rev = r[0];
    var common = Object.keys(num_).filter(function (f) { return f in rev; }).sort();
    if (!common.length) return null;
    var latest = common[common.length - 1], m = QFRAME.exec(latest);
    if (!m) return null;
    var prev = 'CY' + (parseInt(m[1], 10) - 1) + 'Q' + m[2];
    if (!(prev in num_) || !(prev in rev) || !rev[latest] || !rev[prev]) return null;
    var lm = num_[latest] / rev[latest] * 100, pm = num_[prev] / rev[prev] * 100;
    return { frame: latest, latestPct: lm, priorYearPct: pm, changePp: lm - pm, numeratorConcept: n[1], revenueConcept: r[1] };
  }

  function latestQuarterYoy(facts, concepts) {
    var fu = factUnits(facts, concepts), byKey = {}, keys = [];
    fu[0].forEach(function (r) {
      if (['10-Q', '10-K', '20-F', '40-F'].indexOf(r.form) < 0 || !r.fy || !r.fp) return;
      if (r.val === null || r.val === undefined || ['Q1', 'Q2', 'Q3', 'FY'].indexOf(r.fp) < 0) return;
      var key = parseInt(r.fy, 10) + '|' + r.fp, prev = byKey[key];
      if (!prev) keys.push(key);
      if (!prev || rankGe(rank(r), rank(prev))) byKey[key] = r;
    });
    // Python: sorted(by_key.items(), key=(end, (fy, fp)), reverse=True)
    var items = keys.map(function (k) { var p = k.split('|'); return { fy: parseInt(p[0], 10), fp: p[1], row: byKey[k] }; });
    items.sort(function (a, b) {
      var ea = s(a.row.end), eb = s(b.row.end);
      if (ea !== eb) return ea < eb ? 1 : -1;
      if (a.fy !== b.fy) return b.fy - a.fy;
      return a.fp < b.fp ? 1 : a.fp > b.fp ? -1 : 0;
    });
    for (var i = 0; i < items.length; i++) {
      var prevRow = byKey[(items[i].fy - 1) + '|' + items[i].fp];
      if (!prevRow) continue;
      var a = num(items[i].row.val), b = num(prevRow.val);
      if (a === null || b === null) continue;
      if (b > 0) return { a: a, b: b, mode: 'YOY', concept: fu[1] };
      if (b <= 0 && 0 < a) return { a: null, b: null, mode: 'TURNAROUND', concept: fu[1] };
    }
    return { a: null, b: null, mode: null, concept: fu[1] };
  }

  function annualMetrics(facts, concepts) {
    var fu = factUnits(facts, concepts), rows = {};
    fu[0].forEach(function (r) {
      if (['10-K', '20-F', '40-F'].indexOf(r.form) < 0 || r.fp !== 'FY' || r.val === null || r.val === undefined) return;
      if (!r.fy) return;
      var y = parseInt(r.fy, 10), prev = rows[y];
      if (!prev || rankGe(rank(r), rank(prev))) rows[y] = r;
    });
    var annual = {}; Object.keys(rows).forEach(function (y) { annual[y] = parseFloat(rows[y].val); });
    var years = Object.keys(annual).map(Number).sort(function (a, b) { return a - b; });
    return { years: years.slice(-4).map(function (y) { return [y, annual[y]]; }), count: years.length, concept: fu[1] };
  }

  function latestFact(facts, concepts, annualOnly) {
    var fu = factUnits(facts, concepts), allowed = annualOnly ? ['10-K', '20-F', '40-F'] : ['10-Q', '10-K', '20-F', '40-F'];
    var best = null, bestRank = null;
    fu[0].forEach(function (row) {
      if (allowed.indexOf(row.form) < 0 || row.val === null || row.val === undefined) return;
      if (annualOnly && row.fp !== 'FY') return;
      var rk = rank(row);
      // Stable sort then [-1]: the LAST row among equal ranks wins.
      if (best === null || rankGe(rk, bestRank)) { best = parseFloat(row.val); bestRank = rk; }
    });
    return best;
  }

  function summarize(facts, submissions) {
    var recent = ((submissions || {}).filings || {}).recent || {};
    var forms = recent.form || [], acc = recent.accessionNumber || [], docs = recent.primaryDocument || [], dates = recent.filingDate || [];
    // Only the forms the dashboard reads (annual report link + recent catalysts). Insider
    // Form 4s can number in the hundreds and would push the 10-K out of a plain cut.
    var KEEP = ['8-K', '10-Q', '10-K', '20-F', '40-F', '6-K'];
    var filings = [];
    for (var i = 0; i < forms.length && filings.length < 60; i++) {
      if (KEEP.indexOf(forms[i]) < 0) continue;
      filings.push({ form: forms[i], accession: acc[i] || '', doc: docs[i] || '', date: dates[i] || '' });
    }
    var annualIdx = -1;
    for (var j = 0; j < forms.length; j++) if (['10-K', '20-F', '40-F'].indexOf(forms[j]) >= 0 && j < acc.length && j < docs.length) { annualIdx = j; break; }
    return {
      sic: submissions ? submissions.sic : null,
      sicDescription: submissions ? submissions.sicDescription : null,
      annualFiling: annualIdx >= 0 ? { form: forms[annualIdx], accession: acc[annualIdx], doc: docs[annualIdx], date: dates[annualIdx] || '' } : null,
      filings: filings,
      epsYoy: latestQuarterYoy(facts, EPS),
      salesYoy: latestQuarterYoy(facts, SALES),
      annual: annualMetrics(facts, EPS),
      epsHistory: growthHistory(facts, EPS),
      salesHistory: growthHistory(facts, SALES),
      grossMargin: marginChange(facts, ['GrossProfit'], SALES),
      operatingMargin: marginChange(facts, ['OperatingIncomeLoss'], SALES),
      health: {
        assets: latestFact(facts, ['Assets']),
        liabilities: latestFact(facts, ['Liabilities']),
        equity: latestFact(facts, ['StockholdersEquity', 'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest', 'Equity']),
        currentAssets: latestFact(facts, ['AssetsCurrent', 'CurrentAssets']),
        currentLiabilities: latestFact(facts, ['LiabilitiesCurrent', 'CurrentLiabilities']),
        netIncome: latestFact(facts, ['NetIncomeLoss', 'ProfitLoss'], true),
      },
    };
  }

  // Browser only: fetch SEC files for the given tickers (SEC fair-access pace: < 10 requests/second).
  async function run(tickers) {
    var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
    var get = async function (url) { var r = await fetch(url, { headers: { Accept: 'application/json' } }); if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); };
    var map = (window.__secTickerMap = window.__secTickerMap || await get('https://www.sec.gov/files/company_tickers.json'));
    var byTicker = {};
    Object.keys(map).forEach(function (k) { byTicker[String(map[k].ticker).toUpperCase()] = String(map[k].cik_str).padStart(10, '0'); });
    var out = {};
    for (var i = 0; i < tickers.length; i++) {
      var t = String(tickers[i]).toUpperCase(), cik = byTicker[t] || byTicker[t.replace('-', '.')] || byTicker[t.replace('.', '-')];
      if (!cik) { out[t] = { error: 'NO_CIK' }; continue; }
      try {
        var sub = await get('https://data.sec.gov/submissions/CIK' + cik + '.json'); await sleep(150);
        var facts = await get('https://data.sec.gov/api/xbrl/companyfacts/CIK' + cik + '.json'); await sleep(150);
        out[t] = Object.assign({ cik: cik }, summarize(facts, sub));
      } catch (e) { out[t] = { cik: cik, error: String(e && e.message || e).slice(0, 80) }; }
    }
    return out;
  }

  return { summarize: summarize, run: run, num: num };
})();
if (typeof module !== 'undefined') module.exports = SEC_RELAY;
