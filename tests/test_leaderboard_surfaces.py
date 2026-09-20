"""Public board summaries must not manufacture ranks or hand results."""
from html.parser import HTMLParser
from pathlib import Path
import json
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class TableRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], [], None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        if tag in ('td', 'th'):
            self.cell = ''

    def handle_data(self, data):
        if self.cell is not None:
            self.cell += data

    def handle_endtag(self, tag):
        if tag in ('td', 'th'):
            self.row.append(self.cell.strip())
            self.cell = None
        if tag == 'tr':
            self.rows.append(self.row)


class LeaderboardSurfaceTests(unittest.TestCase):
    def test_provisional_rows_match_published_article(self):
        home = (ROOT / 'publishing/index.html').read_text(encoding='utf-8')
        table = re.search(r'<table[^>]+id="provisional-results".*?</table>', home, re.S)
        self.assertIsNotNone(table)
        rows = TableRows()
        rows.feed(table.group())
        self.assertEqual(rows.rows[0], ['Run / effort', 'Matched solids / 101', 'Interfaces / 207', 'GAP median ↓', 'POS relative median ↓', 'ORIENT aligned ↑'])
        article = TableRows()
        article.feed((ROOT / 'publishing/astra-vs-grok/index.html').read_text(encoding='utf-8'))
        for col, row in enumerate(rows.rows[1:], 1):
            self.assertEqual(row[1:], [article.rows[i][col] for i in (1, 2, 5, 3, 7)])
        self.assertEqual(len(rows.rows), 6)
        self.assertEqual([r[0] for r in rows.rows[1:]], [
            'Grok A01 · unavailable', 'Grok A02 · unavailable', 'Grok A03 · unavailable',
            'Astra A05 · Medium', 'Astra A06 · Medium'])
        for text in ('Provisional results · not ranked', 'diagnostic/non-blind', 'model and effort are unavailable', 'smaller matched sets', 'id="historical-board"', 'site snapshot dated 2026-06-11'):
            self.assertIn(text, home)
        registry = json.loads((ROOT / 'results/marb_runs.json').read_text(encoding='utf-8'))
        self.assertEqual(len(registry['runs']), 43)

    def test_hand_board_links_unranked_pilot_and_keeps_cases_separate(self):
        hand = (ROOT / 'publishing/robotic-hand/index.html').read_text(encoding='utf-8')
        for text in ('Robotic hand leaderboard', 'Initial results · unranked', 'Amazing Hand', 'ORCA',
                     'five have placement measurements and one remains ungraded', 'not comparable with M3-CRETE',
                     'id="amazing-hand"', 'id="orca"'):
            self.assertIn(text, hand)
        self.assertNotRegex(hand, r'<td[^>]*>\s*0(?:\.0)?\s*(?:mm|%)')
        self.assertNotIn('<td>', hand)  # No synthetic model-result rows.
        self.assertIn('https://marb.cadclaw.io/robotic-hand/', hand)
        self.assertEqual({p.name for p in (ROOT / 'publishing/robotic-hand').iterdir()}, {'index.html', 'handbench-baseline', 'handbench-technical-report'})
        self.assertIn('/robotic-hand/handbench-baseline/', hand)
        self.assertIn('ORCA still needs a matching authored assembly', hand)
        self.assertNotIn('No evaluation campaign has started', hand)
        for pattern in (r'(?i)(?<![a-z])[a-z]:[\\/]|file://|ssh://', r'\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b', r'(?i)localhost|/workspace/|/home/|\.zip|\.step|\.yaml|GX10'):
            self.assertIsNone(re.search(pattern, hand))

    def test_routes_and_build_allowlist(self):
        for name in ('publishing/index.html', 'publishing/llms.txt', 'publishing/sitemap.xml', 'scripts/build-site.ps1'):
            self.assertIn('robotic-hand', (ROOT / name).read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
