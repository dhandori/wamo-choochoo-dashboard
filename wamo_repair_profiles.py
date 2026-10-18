#!/usr/bin/env python3
"""Repair existing KR company classifications without any network/price refresh.

Default is a dry run; --write changes only embedded company payload and the
business-profile cache, including offline sector grouping/action rebuild.
Callers must regenerate the separate insight/trend outputs.
"""
import argparse
import copy
import json
from pathlib import Path
import wamo_update_business_dart as core


def migrate(payload, cache, fixed_profiles):
    stocks = payload.get('stocks') or []
    identities = {str(x.get('stock_code') or x.get('ticker') or '').split('.')[0]: x for x in stocks}
    changed = []
    for stock in stocks:
        if core.repair_business_classification(stock, fixed_profiles):
            changed.append(str(stock.get('stock_code') or stock.get('ticker')))
    cache_changes = []
    for code, entry in cache.items():
        identity = identities.get(str(code).split('.')[0], {})
        # Cache-only records without an identity cannot validate generated claims.
        candidate = copy.deepcopy(entry)
        identity_keys = ('name', 'stock_code', 'krxSector')
        for key in identity_keys:
            if identity.get(key) is not None:
                candidate.setdefault(key, identity[key])
        core.repair_business_classification(candidate, fixed_profiles)
        # Do not pad cache entries with stock-only fields merely for migration.
        for key in (*identity_keys, 'sector'):
            if key not in entry:
                candidate.pop(key, None)
        if candidate != entry:
            cache[code] = candidate
            cache_changes.append(code)
    return {'changedStocks': changed, 'changedCacheEntries': cache_changes,
            'memoryStocks': [x.get('name') for x in stocks if x.get('detailSector') == '메모리 반도체'],
            'reviewRequired': [x.get('name') for x in stocks if x.get('classificationReviewRequired')]}


def rebuild_derived(payload):
    """Recompute grouping using the snapshot's original benchmark returns only."""
    raw = payload['stocks']
    meta = payload.setdefault('meta', {})
    flow_meta = meta.setdefault('sectorFlowMeta', {})
    benchmarks = flow_meta.get('benchmarks') or {}
    if not benchmarks:
        raise RuntimeError('Snapshot benchmark returns are required for offline sector rebuild')
    sectors = core._build_sector_stats(raw, benchmarks)
    by_name = {sector['name']: sector for sector in sectors}
    for stock in raw:
        stock['sectorAction'] = core._stock_sector_action_overlay(stock, by_name[stock['sector']])
    for sec in sectors:
        members = [x for x in raw if x.get("sector") == sec.get("name")]
        leader_rank = sorted(
            members,
            key=lambda x: (
                1 if x.get("trendTemplate") else 0,
                x.get("conditionCount",0),
                x.get("score",0),
                x.get("rsPercentile",0),
                x.get("volumeRatio",0),
            ),
            reverse=True,
        )
        major_rank = sorted(
            members,
            key=lambda x: x.get("market_cap_krw") or 0,
            reverse=True,
        )
        sec["leaderStock"] = ({
            "name": leader_rank[0].get("name"),
            "ticker": leader_rank[0].get("ticker"),
            "score": leader_rank[0].get("score"),
            "rs": leader_rank[0].get("rsPercentile"),
            "stage2": bool(leader_rank[0].get("trendTemplate")),
            "conditions": leader_rank[0].get("conditionCount",0),
        } if leader_rank else None)
        sec["leaderCandidates"] = [
            {
                "name": m.get("name"), "ticker": m.get("ticker"),
                "score": m.get("score"), "rs": m.get("rsPercentile"),
                "stage2": bool(m.get("trendTemplate")),
                "conditions": m.get("conditionCount",0),
            }
            for m in leader_rank[:4]
        ]
        sec["leaders"] = [m.get("name") for m in leader_rank[:4]]
        # 같은 세부섹터의 시총 상위기업뿐 아니라 현재 주도주도 반드시 보이게 한다.
        major_names = []
        for m in ((leader_rank[:1] if leader_rank else []) + major_rank):
            name = m.get("name")
            if name and name not in major_names:
                major_names.append(name)
            if len(major_names) >= 6:
                break
        sec["majorCompanies"] = major_names

    payload['sectors'] = sectors
    eligible = [sector for sector in sectors
                if sector.get('groupStatus') != 'HOLDING_THEME'
                and sector.get('name') != 'ETF' and (sector.get('flowEligibleCount') or 0) >= 3]
    flow_meta['eligibleSectorCount'] = len(eligible)
    flow_meta['strongSectorCount'] = sum(sector.get('flowStatus') in ('BOTH', 'NEW_7D', 'PERSISTENT_30D') for sector in eligible)
    for key, status in (('bothStrongCount', 'BOTH'), ('new7dCount', 'NEW_7D'), ('persistent30dCount', 'PERSISTENT_30D')):
        flow_meta[key] = sum(sector.get('flowStatus') == status for sector in eligible)
    for key, field in (('expandingCount', 'expanding'), ('concentrationWarningCount', 'concentrationWarning')):
        flow_meta[key] = sum(bool(sector.get(field)) for sector in eligible)
    if 'universeFunnel' in meta:
        meta['universeFunnel']['sectorAction'] = sum((stock.get('sectorAction') or {}).get('status') == 'CONFIRMED' for stock in raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--index', type=Path, default=core.INDEX)
    parser.add_argument('--cache', type=Path, default=core.PROFILE_CACHE)
    parser.add_argument('--write', action='store_true')
    args = parser.parse_args()
    html = args.index.read_text(encoding='utf-8')
    payload = core.extract_old_payload(html)
    if not payload.get('stocks'):
        raise RuntimeError('No embedded stocks found; refusing to write')
    cache = json.loads(args.cache.read_text(encoding='utf-8')) if args.cache.exists() else {}
    original_index = core.INDEX
    try:
        core.INDEX = args.index
        fixed = core._load_fixed_business_db_from_index()
    finally:
        core.INDEX = original_index
    result = migrate(payload, cache, fixed)
    rebuild_derived(payload)
    payload["meta"]["qa"] = core.validate_payload_integrity(payload)
    result["sectorCount"] = len(payload["sectors"])
    result["qaStatus"] = payload["meta"]["qa"].get("status")
    if result["qaStatus"] != "PASS":
        raise RuntimeError('Migration integrity QA did not pass; refusing to write')
    if args.write:
        args.index.write_text(core.replace_payload(html, payload), encoding='utf-8')
        args.cache.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
