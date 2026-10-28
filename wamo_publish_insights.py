"""Build the lightweight radar and its embedded, same-release fallback."""
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import tempfile
from wamo_insights import build_insights
from wamo_update import extract_old_payload
from wamo_runtime import patch_status_ui


def collect_snapshot(root, now=None):
    payloads={}
    for market,page in [('KR','index.html'),('US','us.html')]:
        try:payloads[market]=extract_old_payload((root/page).read_text(encoding='utf-8'))
        except (OSError,ValueError):payloads[market]={}
    try:previous=json.loads((root/'wamo_insights.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):previous=None
    # Dated, source-referenced migration seed; ignored after its exact next session.
    try:baseline=json.loads((root/'radar/signal-baseline.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):baseline=None
    current_time = now or datetime.now(timezone.utc)
    result = build_insights(payloads, current_time, previous, baseline)
    # Japan/Taiwan come from the Lite engine (wamo_asia.py); only their freshness is re-derived here.
    try:
        from wamo_insights import ASIA, refresh_market_status
        try:
            asia = json.loads((root/'wamo_asia_markets.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            asia = previous or {}  # Korea/US runs: carry the last published JP/TW forward.
        for market, block in (asia.get('markets') or {}).items():
            if market in ASIA and isinstance(block, dict) and block.get('stocks'):
                result['markets'][market] = refresh_market_status(block, market, current_time)
    except (OSError, ValueError, TypeError, KeyError, RuntimeError):
        pass  # No Asia file yet, or calendar missing: Korea/US publish as before.
    try:
        from wamo_news import attach_news
        cache = json.loads((root/'wamo_news.json').read_text(encoding='utf-8'))
        attach_news(result, cache, current_time)
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass  # Optional headlines never prevent publication of valid prices.
    return result


def asset_version(path):
    """Content hash used as the cache-busting ?v= value (changes only when the file changes)."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:10]


def version_assets(html, root):
    """Stamp ./radar/ stylesheets and scripts with content hashes and map every radar module.

    Modules import each other without ?v= ('./help.js'); the import map sends each one to its
    hashed URL, so every importer gets the same copy and nobody edits version strings by hand.
    """
    root=Path(root)
    def stamp(match):
        path=root/match.group(2)
        return f'{match.group(1)}"./{match.group(2)}?v={asset_version(path)}"' if path.is_file() else match.group(0)
    html=re.sub(r'((?:href|src)=)"\./(radar/[\w./-]+\.(?:css|js|mjs))"',stamp,html)
    modules={f'./radar/{p.name}':f'./radar/{p.name}?v={asset_version(p)}'
             for p in sorted((root/'radar').glob('*')) if p.suffix in ('.js','.mjs') and p.is_file()}
    importmap=f'<script type="importmap">{json.dumps({"imports":modules},separators=(",",":"))}</script>' if modules else ''
    return html.replace('<!--WAMO_IMPORTMAP-->',importmap)


def render_page(template, snapshot, root):
    """(snapshot JSON, radar.html text). The browser checks reuse this with a fixed snapshot."""
    if template.count('<!--WAMO_INSIGHTS-->')!=1:
        raise ValueError('radar template snapshot marker missing/duplicated')
    data=json.dumps(snapshot,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    safe=data.replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    return data,version_assets(template,root).replace('<!--WAMO_INSIGHTS-->',f'<script type="application/json" id="wamo-snapshot">{safe}</script>')


def publish_snapshot(root=None, now=None):
    root=Path(root or Path(__file__).resolve().parent)
    template=(root/'radar.template.html').read_text(encoding='utf-8')
    snapshot=collect_snapshot(root,now)
    data,rendered=render_page(template,snapshot,root)
    pending=[]
    try:
        for name,content in [('wamo_insights.json',data),('radar.html',rendered)]:
            path=root/name
            if path.exists() and path.read_text(encoding='utf-8')==content:continue
            fd,temp=tempfile.mkstemp(prefix=name+'.',dir=root)
            with os.fdopen(fd,'w',encoding='utf-8') as stream:stream.write(content);stream.flush();os.fsync(stream.fileno())
            pending.append((temp,path))
        for temp,path in pending:os.replace(temp,path)
    finally:
        for temp,_ in pending:
            if os.path.exists(temp):os.unlink(temp)
    print('Radar snapshot:', ' / '.join(f"{m} {v['status']} {v['asOf']} ({len(v['stocks'])})" for m,v in snapshot['markets'].items()))
    return snapshot


def patch_entry_pages(root):
    for market, name in [('KR', 'index.html'), ('US', 'us.html')]:
        path = root / name
        if path.exists():
            html = path.read_text(encoding='utf-8')
            updated = patch_status_ui(html, market)
            if updated != html:
                path.write_text(updated, encoding='utf-8')


if __name__=='__main__':
    root = Path(__file__).resolve().parent
    publish_snapshot(root)
    patch_entry_pages(root)
