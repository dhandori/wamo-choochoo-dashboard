"""매크로 지수 텔레그램은 하루 한 번만 보낸다(2026-11-20: 여러 길이 아침 회차를 겹쳐 걸 수 있음)."""
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path

import wamo_macro as m


class MacroNotifyTests(unittest.TestCase):
    def run_main(self, out, sent):
        orig = (m.OUT, m.HISTORY, m.build, m.telegram_text)
        fake = types.ModuleType('wamo_alerts')
        fake.telegram_send = lambda token, chat, text: sent.append(text)
        sys.modules['wamo_alerts'] = fake
        m.OUT, m.HISTORY = out, out.with_name('h.json')
        m.build = lambda: ({'asOf': 'x'}, [])
        m.telegram_text = lambda d: '본문'
        try:
            import os
            os.environ['TELEGRAM_BOT_TOKEN'], os.environ['TELEGRAM_CHAT_ID'] = 't', 'c'
            m.main(['--telegram'])
        finally:
            m.OUT, m.HISTORY, m.build, m.telegram_text = orig
            sys.modules.pop('wamo_alerts', None)

    def test_sends_once_per_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, sent = Path(tmp) / 'wamo_macro.json', []
            self.run_main(out, sent)
            self.run_main(out, sent)
            self.assertEqual(len(sent), 1)
            self.assertEqual(json.loads(out.read_text())['telegramSent'], datetime.now(m.KST).date().isoformat())

    def test_sends_again_next_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, sent = Path(tmp) / 'wamo_macro.json', []
            out.write_text(json.dumps({'telegramSent': '2000-01-01'}))
            self.run_main(out, sent)
            self.assertEqual(len(sent), 1)


if __name__ == '__main__':
    unittest.main()
