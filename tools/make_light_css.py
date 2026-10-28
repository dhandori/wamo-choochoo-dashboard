"""어두운 판 CSS(style/trend/ftd/leaders/schedule)에서 밝은 테마 CSS(radar/light.css)를 만든다.

색이 직접 적힌 모든 선언을 찾아 밝기를 뒤집은 색으로 바꿔 `:root[data-theme="light"]` 규칙으로 내보낸다.
어두운 판 CSS를 고친 뒤에는 이 파일을 다시 실행해서 light.css를 새로 만든다:  python tools/make_light_css.py
"""
import colorsys
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ['style.css', 'trend.css', 'ftd.css', 'leaders.css', 'schedule.css', 'patterns.css']
PREFIX = ':root[data-theme="light"]'

TOGGLE = '''
/* 밝게/어둡게 버튼: 맨 위 제목 오른쪽에 붙어요 */
.theme-toggle { margin: 0 auto 0 12px; min-height: 32px; padding: 4px 12px; border-radius: 999px; font-size: 13px; white-space: nowrap; }
'''

HEADER = '''/* 밝은 테마. <html data-theme="light">일 때만 적용돼요. 자동 생성 파일이라 직접 고치지 말고
   tools/make_light_css.py 를 고쳐서 다시 만드세요. 색 규칙은 어두운 판과 같아요:
   상승·유입 = 빨강, 하락·유출 = 파랑, 나쁨·경고 = 주황, 좋음 = 초록, 보통 = 노랑 */
:root[data-theme="light"] {
  color-scheme: light;
  --bg: #edf0f4; --panel: #ffffff; --line: #a9b7ca; --muted: #34455b;
  --text: #080f1a; --green: #076b4a; --blue: #1347a0;
  --up: #c4132c; --down: #1553b5; --bad: #a85000; --red: var(--bad);
  --up-bg: rgba(209, 35, 59, .10); --down-bg: rgba(27, 99, 196, .10);
  --amber: #7a5500; --amber-bg: #fff4d6; --green-bg: #e0f4ec; --red-bg: #ffeedb;
  --ink-2: #172536; --ink-3: #3d4f66; --rule: #c9d4e2; --chip: #eef2f8;
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
        l2 = l * 0.52                   # 어두운 바탕 위의 밝은 강조색 -> 흰 바탕에서도 읽히는 진한 색
    elif s < 0.55 and l < 0.35 and not text:
        l2 = 1 - 0.55 * l               # 어두운 회색 바탕은 아주 밝게 (칸막이가 너무 무겁지 않도록)
    else:
        l2 = 1 - l                      # 나머지는 밝기를 뒤집음 (배경은 밝게, 글씨는 어둡게)
    if text:
        l2 = min(l2, 0.22)              # 글씨는 항상 흰 바탕에서 읽힐 만큼 어둡게
    if prop.startswith('border') or prop == 'outline-color':
        l2 = min(l2, 0.74)             # 테두리는 흰 바탕에서도 또렷하게
    sat = min(0.9, s) if l2 >= 0.7 else s * 0.9
    r2, g2, b2 = colorsys.hls_to_rgb(h, l2, sat)
    if a is None:
        return '#%02x%02x%02x' % tuple(round(x * 255) for x in (r2, g2, b2))
    return 'rgba(%d, %d, %d, %s)' % (round(r2 * 255), round(g2 * 255), round(b2 * 255), ('%.2f' % a).rstrip('0').rstrip('.'))


DARK_PREFIX = ':root:not([data-theme="light"])'


def boost(c, prop=''):
    """어두운 테마에서 흐린 글씨를 더 밝게. 글씨(color)만 바꾸고 배경과 선은 그대로 둬요."""
    if prop != 'color':
        return c
    r, g, b, a = parse(c)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    if l < 0.3 or (a is not None and a < 0.5):
        return c                        # 어두운 글씨는 밝은 칩 위에 있는 경우라 건드리지 않음
    if s < 0.35:
        l2 = max(l, 0.78)               # 회색 설명 글씨
    else:
        l2 = max(l, 0.70)               # 색 있는 강조 글씨
    if abs(l2 - l) < 0.01:
        return c
    r2, g2, b2 = colorsys.hls_to_rgb(h, l2, s)
    hexs = '#%02x%02x%02x' % tuple(round(x * 255) for x in (r2, g2, b2))
    return hexs if a is None else 'rgba(%d, %d, %d, %s)' % (round(r2 * 255), round(g2 * 255), round(b2 * 255), ('%.2f' % a).rstrip('0').rstrip('.'))


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


def convert(rules, wrap=None, fn=None, prefix=None):
    fn = fn or flip
    prefix = prefix or PREFIX
    lines = []
    for head, body in rules:
        if head.startswith('@keyframes') or head.startswith('@font-face'):
            continue
        if head.startswith('@'):
            inner = convert(split_rules(body), head, fn, prefix)
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
            new = COLOR.sub(lambda m: fn(m.group(0), prop), value)
            if new != value:
                decls.append(f'{prop}: {new}')
        if not decls:
            continue
        pre = lambda s: f'{prefix} {s[4:].strip()}' if s.startswith('body') and not s.startswith('body.') else f'{prefix} {s}'
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


SHARED = '''
/* ===== 두 테마 공통: 차트와 흐린 요소를 더 진하게 ===== */
.cx-vol.up, .cx-vol.down { opacity: .92; }
.lq-bar.up, .lq-bar.down { opacity: .85; }
.tr-vol.prev { opacity: .62; }
.tr-cal-row:not(.trend) { opacity: .88; }
.tr-stage-step { opacity: .78; }
.tr-gauge-zone { opacity: .75; }
.cx-strip-mark { opacity: .22; }

/* ===== 어두운 테마: 글씨와 칸막이를 조금 더 또렷하게 ===== */
:root:not([data-theme="light"]) {
  --text: #f4f7fb; --muted: #c2cfe0; --line: #34465d; --ink-2: #dde6f1; --ink-3: #b6c5d8;
}
'''


def small_fonts(rules, wrap=None):
    """11px 이하 작은 글씨는 1px씩 키워요. 두 테마 공통."""
    out = []
    for head, body in rules:
        if head.startswith('@keyframes') or head.startswith('@font-face'):
            continue
        if head.startswith('@'):
            inner = small_fonts(split_rules(body), head)
            if inner:
                out.append(head + ' {\n' + inner + '}\n')
            continue
        for d in declarations(body):
            prop, _, value = d.partition(':')
            m = re.fullmatch(r'\s*(\d+(?:\.\d+)?)px\s*', value)
            if prop.strip() == 'font-size' and m and float(m.group(1)) <= 11:
                size = float(m.group(1)) + 1
                sels = ', '.join(f':root {x.strip()}' for x in head.split(','))
                out.append(f'{sels} {{ font-size: {size:g}px; }}\n')
    return ''.join(out)


def main():
    parts = [HEADER, TOGGLE, SHARED]
    for name in SOURCES:
        css = (ROOT / 'radar' / name).read_text(encoding='utf-8')
        rules = split_rules(css)
        parts.append(f'\n/* ===== {name} (밝은 테마) ===== */\n' + convert(rules))
        parts.append(f'\n/* ===== {name} (어두운 테마 글씨 보정) ===== */\n' + convert(rules, fn=boost, prefix=DARK_PREFIX))
        parts.append(f'\n/* ===== {name} (작은 글씨 키우기) ===== */\n' + small_fonts(rules))
    parts.append(FIXES)
    (ROOT / 'radar' / 'light.css').write_text(''.join(parts), encoding='utf-8')
    print('radar/light.css', sum(len(p) for p in parts), 'bytes')


if __name__ == '__main__':
    main()
