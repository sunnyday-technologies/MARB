"""Retain published HandBench outcomes and protect its public artifact boundary."""
from pathlib import Path
import re
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / 'publishing/robotic-hand/handbench-baseline/index.html'


class HandBenchPublicationTests(unittest.TestCase):
    def test_outcomes_and_material_limits(self):
        text = PAGE.read_text('utf-8')
        for required in ('Astra Low unavailable/ungraded', 'Medium 98/12',
                         'Extra high 226/20', 'Grok 01 1/1', 'Grok 02 226/1',
                         'Grok 03 139/1', 'Initial results, unranked', '226/226',
                         'All three Astra attempts timed out',
                         'Medium retains a tool-call accounting qualification',
                         'Ultra was not tested', 'operator-supervised',
                         'No higher-budget comparison', 'future work'):
            self.assertIn(required, text)
        self.assertNotIn('noindex', text)
        self.assertNotIn('LOCAL ARTICLE DRAFT', text)
        self.assertEqual(len(re.findall(r'<figure>', text)), 5)
        self.assertIn('rel="canonical" href="https://marb.cadclaw.io/robotic-hand/handbench-baseline/"', text)

    def test_public_artifact_boundary(self):
        self.assertEqual({p.name for p in PAGE.parent.iterdir()}, {'index.html', 'article.css'})
        text = '\n'.join(p.read_text('utf-8') for p in PAGE.parent.iterdir())
        for pattern in (r'(?i)(?<![a-z])[a-z]:[\\/]|file://|ssh://',
                        r'(?i)localhost|/workspace/|/home/|\.zip|\.yaml|\.step',
                        r'\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b'):
            self.assertIsNone(re.search(pattern, text))

    def test_public_images_match_retained_copies_without_private_metadata(self):
        images = list((ROOT/'publishing/media/handbench').iterdir())
        self.assertEqual(len(images), 5)
        for path in images:
            data = path.read_bytes()
            self.assertEqual(data, (ROOT/'results/figures/handbench'/path.name).read_bytes())
            self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
            offset = 8
            while offset < len(data):
                size = struct.unpack('>I', data[offset:offset+4])[0]
                self.assertNotIn(data[offset+4:offset+8], {b'tEXt', b'zTXt', b'iTXt', b'eXIf', b'tIME'})
                offset += 12+size
            self.assertEqual(offset, len(data))

if __name__ == '__main__':
    unittest.main()
