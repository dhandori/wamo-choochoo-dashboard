"""어두운 판 CSS(style/trend/ftd/leaders/schedule)에서 밝은 테마 CSS(radar/light.css)를 만든다.

색이 직접 적힌 모든 선언을 찾아 밝기를 뒤집은 색으로 바꿔 `:root[data-theme="light"]` 규칙으로 내보낸다.
어두운 판 CSS를 고친 뒤에는 이 파일을 다시 실행해서 light.css를 새로 만든다:  python tools/make_light_css.py
"""
import colorsys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ['style.css', 'trend.css', 'ftd.css', 'leaders.css', 'schedule.css']
PREFIX = ':root[data-theme="light"]'

TOGGLE = '''
/* 밝게/어둡게 버튼: 왼쪽 아래에 떠 있어서 화면 배치를 밀지 않아요 */
.theme-toggle { position: fixed; left: 14px; bottom: 14px; z-index: 30; min-height: 36px; padding: 6px 14px; border-radius: 999px; font-size: 13px; box-shadow: 0 2px 10px rgba(0, 0, 0, .25); }
'''

HEADER = '''/* 밝은 테마. <html data-theme="light">일 때만 적용돼요. 자동 생성 파일이라 직접 고치지 말고
   tools/make_light_css.py 를 고쳐서 다시 만드세요. 색 규칙은 어두운 판과 같아요:
   상승·유입 = 빨강, 하락·유출 = 파랑, 나쁨·경고 = 주황, 좋음 = 초록, 보통 = 노랑 */
:root[data-theme="light"] {
  color-scheme: light;
  --bg: #f3f5f9; --panel: #ffffff; --line: #d3dbe6; --muted: #536479;
  --text: #141d2b; --green: #0b7d5a; --blue: #1a56b3;
  --up: #d1233b; --down: #1b63c4; --bad: #bd5f00; --red: var(--bad);
  --up-bg: rgba(209, 35, 59, .10); --down-bg: rgba(27, 99, 196, .10);
  --amber: #8f6500; --amber-bg: #fff4d6; --green-bg: #e0f4ec; --red-bg: #ffeedb;
  --ink-2: #26364a; --ink-3: #566a82; --rule: #e1e7f0; --chip: #eef2f8;
  --ma20: #b36f00; --ma50: #1f6fd1; --ma150: #0e8a5f; --ma200: #c2336c;
}
'''

COLOR = re.compile(r'#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b|rgba?\(\s*[\d.]+\s*,\s*[\d.]+\s*,\s*[\d.]+\s*(?:,\s*[\d.]+\s*)?\)')


def parse(c):
    if c.startswith('#'):
        h = c[1:]
        if len(h) == 3:
            h = ''.join(x * 2 for x in h)
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
        a = int(h[6:8], 16) / 255 if len(h) == 8 else None
        return r, g, b, a
    nums = [float(x) for x in re.findall(r'[\d.]+', c)]
    return nums[0] / 255, nums[1] / 255, nums[2] / 255, nums[3] if len(nums) > 3 else None


def flip(c, prop=''):
    r, g, b, a = parse(c)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    text = prop == 'color'
    if s > 0.45 and 0.45 < l <= 0.8:
        l2 = l * 0.6                    # 어두운 바탕 위의 밝은 강조색 -> 흰 바탕에서도 읽히는 진한 색
    elif s < 0.55 and l < 0.35 and not text:
        l2 = 1 - 0.55 * l               # 어두운 회색 바탕은 아주 밝게 (칸막이가 너무 무겁지 않도록)
    else:
        l2 = 1 - l                      # 나머지는 밝기를 뒤집음 (배경은 밝게, 글씨는 어둡게)
    if text:
        l2 = min(l2, 0.31)              # 글씨는 항상 흰 바탕에서 읽힐 만큼 어둡게
    sat = min(0.9, s) if l2 >= 0.7 else s * 0.9
    r2, g2, b2 = colorsys.hls_to_rgb(h, l2, sat)
    if a is None:
        return '#%02x%02x%02x' % tuple(round(x * 255) for x in (r2, g2, b2))
    return 'rgba(%d, %d, %d, %s)' % (round(r2 * 255), round(g2 * 255), round(b2 * 255), ('%.2f' % a).rstrip('0').rstrip('.'))


def split_rules(css):
    """최상위 규칙(선택자 { 내용 }) 목록. @media 안은 재귀로 푼다."""
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    out, i, n = [], 0, len(css)
    while i < n:
        j = css.find('{', i)
        if j < 0:
            break
        head, depth, k = css[i:j].strip(), 1, j + 1
        while k < n and depth:
            depth += {'{': 1, '}': -1}.get(css[k], 0)
            k += 1
        out.append((head, css[j + 1:k - 1]))
        i = k
    return out


def declarations(body):
    return [d.strip() for d in re.split(r';(?![^()]*\))', body) if ':' in d]


def convert(rules, wrap=None):
    lines = []
    for head, body in rules:
        if head.startswith('@keyframes') or head.startswith('@font-face'):
            continue
        if head.startswith('@'):
            inner = convert(split_rules(body), head)
            if inner:
                lines.append(head + ' {\n' + inner + '}\n')
            continue
        sels = [s.strip() for s in head.split(',')]
        if any(s.startswith(':root') for s in sels):
            continue
        decls = []
        for d in declarations(body):
            prop, _, value = d.partition(':')
            prop, value = prop.strip(), value.strip()
            if prop.startswith('--') or not COLOR.search(value):
                continue
            new = COLOR.sub(lambda m: flip(m.group(0), prop), value)
            if new != value:
                decls.append(f'{prop}: {new}')
        if not decls:
            continue
        pre = lambda s: f'{PREFIX} {s[4:].strip()}' if s.startswith('body') and not s.startswith('body.') else f'{PREFIX} {s}'
        lines.append(', '.join(pre(s) for s in sels) + ' { ' + '; '.join(decls) + '; }\n')
    return ''.join(lines)


FIXES = '''
/* ===== 진한 색 바탕 위 글씨는 흰색 유지 ===== */
:root[data-theme="light"] .ftd-flow li.now .ftd-flow-no,
:root[data-theme="light"] .ftd-flow li em { color: #ffffff; }
:root[data-theme="light"] .cx-banner.bad .cx-banner-icon,
:root[data-theme="light"] .cx-banner.warn .cx-banner-icon,
:root[data-theme="light"] .cx-banner.lead .cx-banner-icon { color: #ffffff; }

/* ===== JS가 직접 칠하는 차트 색 (밝은 바탕에서 안 보이는 흰색 계열을 진한 색으로) ===== */
:root[data-theme="light"] svg [stroke="#ecf3fc"], :root[data-theme="light"] svg [stroke="#eaf0f8"] { stroke: #1b2635; }
:root[data-theme="light"] svg [fill="#ecf3fc"], :root[data-theme="light"] svg [fill="#eaf0f8"] { fill: #1b2635; }
:root[data-theme="light"] svg [stroke="#27364a"] { stroke: #dde4ee; }
:root[data-theme="light"] svg [stroke="#3a4c63"], :root[data-theme="light"] svg [stroke="#5a6d86"] { stroke: #a9b6c7; }
:root[data-theme="light"] svg [fill="#9fb3c9"], :root[data-theme="light"] svg [fill="#8aa0b9"], :root[data-theme="light"] svg [fill="#9aacc3"] { fill: #4a5a6e; }
:root[data-theme="light"] svg [stroke="#9aacc3"] { stroke: #6b7c92; }
:root[data-theme="light"] i[style*="#ecf3fc"], :root[data-theme="light"] i[style*="#eaf0f8"] { background: #1b2635 !important; }
:root[data-theme="light"] i[style*="#9aacc3"], :root[data-theme="light"] i[style*="#8aa0b9"] { background: #6b7c92 !important; }
'''


def main():
    parts = [HEADER, TOGGLE]
    for name in SOURCES:
        css = (ROOT / 'radar' / name).read_text(encoding='utf-8')
        parts.append(f'\n/* ===== {name} ===== */\n' + convert(split_rules(css)))
    parts.append(FIXES)
    (ROOT / 'radar' / 'light.css').write_text(''.join(parts), encoding='utf-8')
    print('radar/light.css', sum(len(p) for p in parts), 'bytes')


if __name__ == '__main__':
    main()
