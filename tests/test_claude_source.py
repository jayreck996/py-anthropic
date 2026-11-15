import asyncio
from datetime import date
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup
from app.claude import article_author, extract_claude_body, is_article_url, listing_resources
import run_daily

FIXTURES = Path(__file__).parent / 'fixtures'


class ClaudeSourceTests(unittest.TestCase):
    def test_real_article_preserves_headings_lists_links_and_image_positions(self):
        body = extract_claude_body((FIXTURES / 'always-on-agents-rsc.html').read_text(),
                                  'https://claude.com/resources/articles/building-always-on-agents')
        soup = BeautifulSoup(body, 'lxml')
        self.assertEqual(len(soup.select('p')), 39)
        self.assertEqual(len(soup.select('h2,h3')), 9)
        self.assertEqual(len(soup.select('li')), 22)
        self.assertEqual(len(soup.select('img')), 7)
        self.assertEqual(len(soup.select('a')), 11)
        self.assertTrue(soup.select('img')[0].find_previous('p').text.startswith('In reality, the agent spends most of its time asleep'))
        self.assertTrue(soup.select('img')[1].find_previous('p').text.startswith('The inbox should track'))
        self.assertEqual(soup.a['href'], 'https://www.anthropic.com/research/building-effective-agents')
        self.assertNotIn('Related articles', body)
        self.assertNotIn('Get the developer newsletter', body)

    def test_video_is_linked_at_its_source_position(self):
        body = extract_claude_body((FIXTURES / 'interactive-connectors-rsc.html').read_text(),
                                  'https://claude.com/resources/articles/interactive-connectors-full-tool')
        soup = BeautifulSoup(body, 'lxml')
        video = soup.select_one('a[href="https://www.youtube.com/watch?v=Gy1jSeSzAhk"]')
        self.assertIsNotNone(video)
        self.assertTrue(video.find_previous('p').text.startswith('This is rolling out'))
        self.assertEqual(len(soup.select('p')), 11)
        self.assertEqual(len(soup.select('h2')), 3)
        self.assertEqual(len(soup.select('img')), 1)
        self.assertEqual(len(soup.select('a')), 8)

    def test_author_is_read_from_the_article_sidebar(self):
        soup = BeautifulSoup('<li class="DetailSidebar-module__item">'
                             '<span class="DetailSidebar-module__label">Author</span>'
                             '<span class="DetailSidebar-module__value">William Hu</span></li>', 'lxml')
        self.assertEqual(article_author(soup), 'William Hu')

    def test_no_generic_dom_fallback(self):
        self.assertIsNone(extract_claude_body('<article><p>Readable DOM only</p></article>',
                                            'https://claude.com/resources/articles/example'))

    def test_featured_and_paginated_articles_cover_the_seven_day_window(self):
        initial = listing_resources((FIXTURES / 'claude-listing-rsc.html').read_text())
        second = json.loads((FIXTURES / 'claude-list-page-2.json').read_text())
        with patch.object(run_daily, '_read_listing', return_value=initial), \
                patch.object(run_daily, '_read_resource_page', return_value=(second['items'], second['total'])) as read_page, \
                patch.object(run_daily, 'date') as clock:
            clock.today.return_value = date(2026, 11, 15)
            clock.fromisoformat.side_effect = date.fromisoformat
            urls = asyncio.run(run_daily.discover_listing('https://claude.com/resources/articles'))
        self.assertEqual(len(urls), 12)
        self.assertEqual(len(set(urls)), 12)
        self.assertIn('https://claude.com/resources/articles/building-always-on-agents', urls)
        self.assertIn('https://claude.com/resources/articles/jpmorganchase-expands-claude-code', urls)
        self.assertNotIn('https://claude.com/resources/articles/introducing-personal-finance-in-claude', urls)
        self.assertTrue(all(is_article_url(url) for url in urls))
        read_page.assert_called_once_with(2)

    def test_pagination_cannot_silently_repeat_a_page(self):
        resources = [{'slug': 'new-article', 'date': date.today().isoformat(), 'externalUrl': None}]
        with patch.object(run_daily, '_read_listing', return_value=(resources, 99)), \
                patch.object(run_daily, '_read_resource_page', return_value=(resources, 99)):
            with self.assertRaisesRegex(ValueError, 'pagination did not advance'):
                asyncio.run(run_daily.discover_listing('https://claude.com/resources/articles'))

    def test_missing_listing_payload_is_an_error(self):
        with self.assertRaisesRegex(ValueError, 'listing was not found'):
            listing_resources('<a href="/resources/articles/example">DOM only</a>')

    def test_url_validation_accepts_only_claude_article_pages(self):
        self.assertTrue(is_article_url('https://claude.com/resources/articles/example-post'))
        self.assertTrue(is_article_url('https://www.claude.com/resources/articles/example-post/'))
        for url in ['https://claude.com/resources/articles', 'http://claude.com/resources/articles/example',
                    'https://claude.com.evil.test/resources/articles/example',
                    'https://www.anthropic.com/news/example', 'https://claude.com/resources/guides/example']:
            with self.subTest(url=url):
                self.assertFalse(is_article_url(url))


if __name__ == '__main__':
    unittest.main()
