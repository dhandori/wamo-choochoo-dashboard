"""Telegram alerts from wamo_trend.json (free; optional).

Sends one message per market per trading day, only when the repository has
TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID secrets. Without them this module does
nothing. wamo_watchlist.json lists the stocks you care about most.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import urllib.parse
import urllib.request

STATE = 'wamo_alert_state.json'
WATCHLIST = 'wamo_watchlist.json'
LIGHT = {'GREEN': '🟢', 'YELLOW': '🟡', 'RED': '🔴', 'UNKNOWN': '⚪'}
TITLE = {'KR': '한국장', 'US': '미국장', 'JP': '일본장', 'TW': '대만장'}
# Same words as the radar's FTD tab (radar/ftd-model.mjs PHASES).
FTD_PHASE = {'OUTSIDE': '하락장 아님', 'CORRECTION': '하락장', 'RALLY': '반등 시도 중',
             'LATE': '반등 시도 중 · 늦은 구간', 'CONFIRMED': 'FTD 나옴 · 상승 전환 신호',
             'PRESSURE': 'FTD 흔들림 · 경고', 'UNAVAILABLE': '판정 보류'}
FTD_FRESH_SESSIONS = 3  # only alert on an FTD from the last few sessions (catch-up after a failed run)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def site_url():
    if os.getenv('WAMO_SITE_URL'):
        return os.environ['WAMO_SITE_URL'].rstrip('/') + '/'
    owner, _, repo = (os.getenv('GITHUB_REPOSITORY') or 'dhandori/wamo-choochoo-dashboard').partition('/')
    return f'https://{owner}.github.io/{repo}/'


def watch_symbols(watchlist, market):
    items = watchlist.get(market) if isinstance(watchlist, dict) else None
    return {str(x).strip().upper() for x in items or [] if str(x).strip()}


def compose(market, block, watchlist):
    w = block.get('weather') or {}
    lines = [f"📊 WAMO {TITLE[market]} 추세 알림 · {block.get('asOf')} 종가",
             f"시장 날씨: {LIGHT.get(w.get('level'), '⚪')} {w.get('label', '판단 보류')}",
             w.get('advice', '')]
    for f in w.get('factors', [])[:4]:
        lines.append(f"· {f['text']}")
    for idx in (block.get('ftd') or {}).get('indexes', []):
        if idx.get('status') != 'OK':
            continue
        phase = idx.get('phase')
        extra = f" (Day {idx['rally']['day']})" if phase in ('RALLY', 'LATE') and idx.get('rally') else ''
        ev = idx.get('ftd') or {}
        if phase == 'CONFIRMED' and ev and volume_estimate(idx, ev):
            extra = ' (잠정 · 거래량 확정 전)'
        lines.append(f"FTD 상태 · {idx.get('label', idx.get('symbol', '지수'))}: {FTD_PHASE.get(phase, phase)}{extra}")

    watch = watch_symbols(watchlist, market)
    if watch:
        notes = []
        for item in (block.get('sellWarnings') or {}).get('items', []):
            if item['symbol'].upper() in watch:
                notes.append(f"⚠️ {item['name']}: {', '.join(r['label'] for r in item['reasons'])}")
        for item in (block.get('rsLeaders') or {}).get('items', []):
            if item['symbol'].upper() in watch:
                notes.append(f"🚀 {item['name']}: RS선 신고가" + (' (주가보다 먼저)' if item.get('leading') else ''))
        for item in (block.get('earnings') or {}).get('upcoming', []):
            if item['symbol'].upper() in watch and item['daysUntil'] <= 5:
                notes.append(f"📅 {item['name']}: 실적발표 D-{item['daysUntil']} ({item['date']} {item['time']})")
        lines.append('')
        lines.append('⭐ 관심 종목')
        lines.extend(notes or ['· 오늘은 특별한 신호가 없어요.'])

    leaders = [x for x in (block.get('rsLeaders') or {}).get('items', []) if x.get('leading')][:5]
    if leaders:
        lines.append('')
        lines.append('🚀 RS선 선행 신고가 (지수보다 강한 종목)')
        for x in leaders:
            lines.append(f"· {x['name']} — 고점까지 {x['highDistancePct']:.0f}% · RS {x['rs'] or 0:.0f}")

    strong = [x for x in (block.get('sellWarnings') or {}).get('items', []) if x.get('severity') == 'HIGH'][:5]
    if strong:
        lines.append('')
        lines.append('⚠️ 강한 경고 (추세가 꺾일 수 있는 종목)')
        for x in strong:
            lines.append(f"· {x['name']} — {', '.join(r['label'] for r in x['reasons'][:2])}")

    soon = [x for x in (block.get('earnings') or {}).get('upcoming', []) if x['daysUntil'] <= 3 and x.get('inTrend')][:6]
    if soon:
        lines.append('')
        lines.append('📅 3일 안에 실적발표 (추세 종목)')
        for x in soon:
            lines.append(f"· {x['name']} — D-{x['daysUntil']} {x['time']}")

    lines.append('')
    lines.append(f"자세히 보기: {site_url()}radar.html?market={market}&tab=trend")
    text = '\n'.join(line for line in lines if line is not None)
    return text[:4000]


def fresh_ftds(market, block):
    """[(key, index, event)] for FTD events in the last few sessions of each index."""
    found = []
    for idx in (block.get('ftd') or {}).get('indexes', []):
        if idx.get('status') not in ('OK', 'STALE'):
            continue
        recent = {r.get('date') for r in (idx.get('series') or [])[-FTD_FRESH_SESSIONS:]}
        for ev in idx.get('events') or []:
            if ev.get('type') == 'FTD' and ev.get('date') in recent:
                found.append((f"{market}:{idx.get('symbol')}:{ev['date']}", idx, ev))
    return found


def volume_estimate(idx, ev):
    """The FTD rests on an estimated volume: today's Yahoo volume is still the first (preliminary)
    figure and, as observed, it is NOT above yesterday's final volume. The model scales it by
    yesterday's own revision factor (wamo_ftd.same_basis_latest), so the FTD may still disappear
    once today's volume is final. Returns (raw ratio, scale) or None when nothing is estimated."""
    p = idx.get('volumePrelim') or {}
    scale, ratio = p.get('scale'), ev.get('volumeRatio')
    if p.get('date') != ev.get('date') or not scale or not ratio:
        return None
    raw = ratio / scale
    return (raw, scale) if raw <= 1 else None


def compose_ftd(market, idx, ev):
    q = sum(1 for ok in (not ev.get('late'), (ev.get('changePct') or 0) >= 2.0,
                         (ev.get('volumeVsAvg') or 0) > 1) if ok)
    name = idx.get('label') or idx.get('symbol')
    est = volume_estimate(idx, ev)
    if est:
        raw, scale = est
        lines = [f"🟠 잠정 FTD · {TITLE.get(market, market)} {name} · {ev['date']} 종가 (거래량 확정 전)",
                 f"반등 {ev.get('rallyDay')}일째에 {ev.get('changePct', 0):+.2f}% 올랐어요. 상승 조건은 채웠어요.",
                 f"거래량은 아직 확정 전이에요. 지금 숫자는 전일의 {raw:.2f}배라 전일보다 많지 않아요. "
                 f"전날 거래량이 확정되며 늘어난 비율({scale:.2f}배)만큼 늘면 전일의 {ev.get('volumeRatio', 0):.2f}배가 돼요.",
                 '거래량이 확정되면 FTD가 맞는지 다시 알려 드려요.']
    else:
        lines = [f"🚨 FTD 발생 · {TITLE.get(market, market)} {name} · {ev['date']} 종가",
                 f"반등 {ev.get('rallyDay')}일째에 {ev.get('changePct', 0):+.2f}% 상승, 거래량은 전일의 {ev.get('volumeRatio', 0):.2f}배예요."]
    if ev.get('volumeVsAvg'):
        lines.append(f"50일 평균 거래량의 {ev['volumeVsAvg']:.2f}배")
    lines.append(f"신호 품질 {q}/3" + (" · 늦은 FTD(반등 8일째 이후)" if ev.get('late') else ''))
    lines.append('')
    lines.append('하락장이 끝나고 상승으로 바뀔 수 있다는 첫 신호예요. 보통은 총자금 10% 이내 시험 매수부터 해요.')
    if ev.get('low'):
        lines.append(f"FTD 당일 저가 {ev['low']:,.2f} 아래로 마감하면 신호가 약해진 거예요.")
    lines.append('WAMO 고정 규칙에 따른 참고 신호이고, 상승을 보장하지 않아요.')
    lines.append('')
    lines.append(f"자세히 보기: {site_url()}radar.html?market={market}&tab=ftd")
    return '\n'.join(lines)[:4000]


def ftd_followups(market, block, sent_keys, provisional, closed):
    """Messages for FTDs already alerted: confirmed once the volume is final, or withdrawn when the
    model no longer finds the FTD on that day. Returns [(key, kind, text)]."""
    out = []
    for idx in (block.get('ftd') or {}).get('indexes', []):
        if idx.get('status') not in ('OK', 'STALE'):
            continue
        sym = idx.get('symbol')
        recent = [r.get('date') for r in (idx.get('series') or [])[-FTD_FRESH_SESSIONS:]]
        prelim_date = (idx.get('volumePrelim') or {}).get('date')
        ftd_by_date = {ev.get('date'): ev for ev in idx.get('events') or [] if ev.get('type') == 'FTD'}
        name = idx.get('label') or sym
        for key in sent_keys:
            m, _, rest = key.partition(':')
            s, _, day = rest.rpartition(':')
            if m != market or s != sym or key in closed or day not in recent:
                continue
            ev = ftd_by_date.get(day)
            if ev is None and day != prelim_date:
                out.append((key, 'withdrawn', '\n'.join([
                    f"↩️ FTD 취소 · {TITLE.get(market, market)} {name} · {day}",
                    f"{day[5:].replace('-', '/')}에 보낸 FTD 알림을 거둬들여요. 거래량이 확정되고 다시 계산해 보니 FTD 조건(전일보다 많은 거래량)을 채우지 못했어요.",
                    '아직 반등 시도 중이에요. 다음 FTD를 기다려요.',
                    '',
                    f"자세히 보기: {site_url()}radar.html?market={market}&tab=ftd"])[:4000]))
            elif ev is not None and key in provisional and not volume_estimate(idx, ev):
                out.append((key, 'confirmed', '\n'.join([
                    f"✅ FTD 확정 · {TITLE.get(market, market)} {name} · {day}",
                    f"거래량이 확정됐어요. {day[5:].replace('-', '/')} 거래량은 전일의 {ev.get('volumeRatio', 0):.2f}배로, 잠정으로 알려 드린 FTD가 맞아요.",
                    '',
                    f"자세히 보기: {site_url()}radar.html?market={market}&tab=ftd"])[:4000]))
    return out


def telegram_send(token, chat_id, text, opener=None):
    body = urllib.parse.urlencode({'chat_id': chat_id, 'text': text,
                                   'disable_web_page_preview': 'true'}).encode()
    req = urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', data=body,
                                 headers={'Content-Type': 'application/x-www-form-urlencoded'})
    with (opener or urllib.request.urlopen)(req, timeout=15) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    if not data.get('ok'):
        raise RuntimeError('telegram rejected')


def send_for_markets(root, markets, sender=telegram_send, env=None):
    """Return files to publish (the dedupe state) when something was sent."""
    env = env if env is not None else os.environ
    token, chat = env.get('TELEGRAM_BOT_TOKEN'), env.get('TELEGRAM_CHAT_ID')
    if not token or not chat:
        print('텔레그램 알림 꺼짐 · TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 비밀값 없음', flush=True)
        return []
    root = Path(root)
    trend = read_json(root / 'wamo_trend.json', {}).get('markets', {})
    watchlist = read_json(root / WATCHLIST, {})
    state = read_json(root / STATE, {'sent': {}})
    sent_any = False
    ftd_sent = state.setdefault('ftdSent', [])
    provisional = state.setdefault('ftdProvisional', [])   # alerted as 잠정 (volume estimated)
    closed = state.setdefault('ftdClosed', [])             # confirmed or withdrawn already told
    for market in markets:
        block = trend.get(market) or {}
        for key, kind, text in ftd_followups(market, block, list(ftd_sent), provisional, closed):
            try:
                sender(token, chat, text)
                closed.append(key)
                del closed[:-50]
                if key in provisional:
                    provisional.remove(key)
                state['updatedAt'] = datetime.now(timezone.utc).isoformat()
                sent_any = True
                print(f'텔레그램 FTD {"확정" if kind == "confirmed" else "취소"} 알림 전송 · {key}', flush=True)
            except Exception as exc:  # noqa: BLE001 - never print the token
                print(f'텔레그램 FTD 후속 알림 실패 · {key}: {type(exc).__name__}', flush=True)
        for key, idx, ev in fresh_ftds(market, block):
            if key in ftd_sent:
                continue
            try:
                sender(token, chat, compose_ftd(market, idx, ev))
                ftd_sent.append(key)
                del ftd_sent[:-50]
                if volume_estimate(idx, ev):
                    provisional.append(key)
                    del provisional[:-20]
                state['updatedAt'] = datetime.now(timezone.utc).isoformat()
                sent_any = True
                print(f'텔레그램 FTD 알림 전송 · {key}', flush=True)
            except Exception as exc:  # noqa: BLE001 - never print the token
                print(f'텔레그램 FTD 알림 실패 · {key}: {type(exc).__name__}', flush=True)
    for market in markets:
        block = trend.get(market)
        if not block or market not in ('KR', 'US') or state['sent'].get(market) == block.get('asOf'):
            continue
        try:
            sender(token, chat, compose(market, block, watchlist))
            state['sent'][market] = block.get('asOf')
            state['updatedAt'] = datetime.now(timezone.utc).isoformat()
            sent_any = True
            print(f'텔레그램 알림 전송 · {market} {block.get("asOf")}', flush=True)
        except Exception as exc:  # noqa: BLE001 - never print the token
            print(f'텔레그램 알림 실패 · {market}: {type(exc).__name__}', flush=True)
    if not sent_any:
        return []
    (root / STATE).write_text(json.dumps(state, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    return [STATE]


if __name__ == '__main__':
    import sys
    root = Path(__file__).resolve().parent
    if '--preview' in sys.argv:
        trend = read_json(root / 'wamo_trend.json', {}).get('markets', {})
        for m, block in trend.items():
            print(compose(m, block, read_json(root / WATCHLIST, {})))
            print('-' * 40)
    else:
        send_for_markets(root, ['KR', 'US'])
