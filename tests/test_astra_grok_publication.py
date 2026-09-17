"""The editorial comparison must retain scores and privacy boundaries."""
from html.parser import HTMLParser
from pathlib import Path
import re
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / 'publishing/astra-vs-grok/index.html'

class Rows(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows=[]; self.row=[]; self.cell=None
    def handle_starttag(self, tag, attrs):
        if tag == 'tr': self.row=[]
        if tag in ('th','td'): self.cell=''
    def handle_data(self, data):
        if self.cell is not None: self.cell += data
    def handle_endtag(self, tag):
        if tag in ('th','td'): self.row.append(self.cell); self.cell=None
        if tag == 'tr': self.rows.append(self.row)

class PublicationTests(unittest.TestCase):
    def test_score_table_and_disclosures(self):
        html = PAGE.read_text(encoding='utf-8')
        parser=Rows(); parser.feed(html)
        self.assertEqual([r[1:] for r in parser.rows[1:8]], [
            ['101','101','100','94','96'],
            ['207','207','203','169','191'],
            ['44.3 mm','53.9 mm','79.3 mm','20.6 mm','20.3 mm'],
            ['84.2 mm','94.7 mm','88.5 mm','70.9 mm','64.3 mm'],
            ['0.0 mm','5.0 mm','5.0 mm','0.0 mm','0.0 mm'],
            ['55.1%','39.1%','42.4%','72.8%','75.9%'],
            ['60.8% (31/51)','72.5% (37/51)','46.0% (23/50)','65.9% (29/44)','67.4% (31/46)'],
        ])
        for required in ('Diagnostic/non-blind','Medium','model and effort unavailable','official leaderboard is unchanged','no shell transcript','not third-party testing'):
            self.assertIn(required,html)
        self.assertNotIn('noindex',html)
        self.assertIn('https://marb.cadclaw.io/astra-vs-grok/',html)

    def test_minimal_public_files_and_no_private_identifiers(self):
        folder=PAGE.parent
        self.assertEqual({p.name for p in folder.iterdir()},{'index.html','story.css'})
        text='\n'.join(p.read_text(encoding='utf-8') for p in folder.iterdir())
        for pattern in (r'(?i)(?<![a-z])[a-z]:[\\/]|file://|ssh://',r'\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b',r'(?i)\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b',r'(?i)localhost|/workspace/|/home/|evidence\.json|runtime\.json|\.zip|\.yaml'):
            self.assertIsNone(re.search(pattern,text))

    def test_public_pngs_have_no_text_exif_or_time_metadata(self):
        files=list((ROOT/'publishing/media/astra-vs-grok').iterdir())
        self.assertEqual(len(files),7)
        for path in files:
            data=path.read_bytes(); self.assertEqual(data[:8],b'\x89PNG\r\n\x1a\n')
            self.assertEqual(data,(ROOT/'results/figures/astra-vs-grok'/path.name).read_bytes())
            offset=8
            while offset<len(data):
                size=struct.unpack('>I',data[offset:offset+4])[0]
                self.assertNotIn(data[offset+4:offset+8],{b'tEXt',b'zTXt',b'iTXt',b'eXIf',b'tIME'})
                offset+=12+size
            self.assertEqual(offset,len(data))

if __name__ == '__main__': unittest.main()
