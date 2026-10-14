import unittest
from datetime import datetime,timezone
from wamo_news import parse_feed,attach_news
NOW=datetime(2026,10,14,tzinfo=timezone.utc)
class RelatedHeadlines(unittest.TestCase):
    def test_preserves_dates_and_rejects_old_future_and_unsafe_links(self):
        def item(date,url='https://news.google.com/rss/articles/abc'):
            return f'<item><title>Headline</title><link>{url}</link><pubDate>{date}</pubDate><source>Publisher</source></item>'
        xml='<rss><channel>'+item('Tue, 13 Oct 2026 12:00:00 GMT')+item('Sun, 01 Jan 2023 00:00:00 GMT')+item('Thu, 15 Oct 2026 12:00:00 GMT')+item('Tue, 13 Oct 2026 12:00:00 GMT','https://[broken')+'</channel></rss>'
        rows=parse_feed(xml,NOW)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['date'],'2026-10-13T12:00:00+00:00')
    def test_old_news_cannot_be_presented_as_current(self):
        snapshot={'markets':{'US':{'stocks':[{'id':'US:A'}]}}}
        cache={'stocks':{'US:A':{'items':[{'title':'Old','date':'2025-10-13T12:00:00+00:00','url':'https://news.google.com/rss/articles/a'}]}}}
        self.assertEqual(attach_news(snapshot,cache,NOW)['markets']['US']['stocks'][0]['news'],[])
