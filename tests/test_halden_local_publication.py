"""Guard the public Halden development update and its evidence boundary."""
from pathlib import Path
import hashlib
import json
import re
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
ROUTE = "/robotic-hand/halden-local-update/"
PAGE = ROOT / "publishing" / ROUTE.strip("/") / "index.html"


class HaldenLocalPublicationTests(unittest.TestCase):
    def test_material_results_and_limits(self):
        text = PAGE.read_text("utf-8")
        for required in (
            "Development results, unranked", "0 of 48 scored groups",
            "49 rigid groups comprising 90 solids", "1 mm placement tolerance",
            "qwen3-vl:32b with Ollama 0.32.1", "4.47 minutes",
            "12 model requests", "11 CAD or inspection requests",
            "64 model requests and 61 CAD", "16 of 100 instances",
            "5 mm distance screen", "Astra Medium (gpt-6-astra)",
            "14.96 minutes", "earlier failed hand attempt",
            "same frozen geometry", "cannot rank Astra against Qwen",
            "Nightly scheduling and cross-host qualification remain outstanding",
            "No DexHand CAD or imagery is redistributed",
        ):
            self.assertIn(required, text)
        self.assertEqual(text.count("<tr><td>"), 6)
        self.assertEqual(text.count("<td>Not graded</td>"), 4)
        self.assertEqual(text.count("<td>0 / 48</td>"), 2)
        self.assertEqual(text.count("<figure"), 3)
        self.assertIn("hand-hero", text)
        self.assertIn("not an in-run checkpoint", text)
        self.assertNotIn("assets/halden-reference-front.png", text)
        self.assertNotIn("assets/qwen-return-front.png", text)
        self.assertNotIn("assets/halden-reference-oblique.png", text)
        self.assertNotIn("assets/qwen-return-oblique.png", text)
        self.assertIn("Colors distinguish mesh components, not materials", text)
        self.assertIn("not a benchmark input", text)
        self.assertIn('property="og:image" content="https://marb.cadclaw.io/robotic-hand/halden-local-update/assets/halden-reference-highfive.png"', text)
        self.assertNotIn("<script", text.lower())

    def test_public_files_and_metadata(self):
        self.assertEqual(
            {p.relative_to(PAGE.parent).as_posix() for p in PAGE.parent.rglob("*") if p.is_file()},
            {"index.html", "article.css", "assets/halden-reference-front.png", "assets/qwen-return-front.png",
             "assets/halden-reference-oblique.png", "assets/qwen-return-oblique.png",
             "assets/halden-reference-highfive.png", "assets/qwen-return-highfive.png"},
        )
        expected = {
            "halden-reference-highfive.png": "8e2d506de0c9e24611dc7788eec7780465451f28c9f702a59bcfaf573ff91835",
            "qwen-return-highfive.png": "6cffbabd0c4e6a01da6d1f4a66f54d003e5b872bba5d0d30bc8e2c190ca3a7a8",
            "halden-reference-oblique.png": "b793dac34e6aad12f96f5a7236658807a2ac1a84bb2ec17b7781b50b135b3905",
            "qwen-return-oblique.png": "126d4337e555ee8addd58273ae40dee28f9ac7ed20673e3f3594c2834713f7f2",
            "halden-reference-front.png": "3a3d454bc3ee8e2395aae823ee3cb45c210b5f515395d0109ea72cdb0c30ccad",
            "qwen-return-front.png": "911c23477dfc0acd36f01d80e39b71dd226c8673e02c358fff07dc45657e1ed9",
        }
        rights = json.loads((ROOT / "publication-rights.json").read_text("utf-8"))
        for name, digest in expected.items():
            path = PAGE.parent / "assets" / name
            data = path.read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
            self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
            offset = 8
            while offset < len(data):
                size = struct.unpack(">I", data[offset:offset + 4])[0]
                self.assertIn(data[offset + 4:offset + 8], {b"IHDR", b"IDAT", b"IEND"})
                offset += size + 12
            self.assertEqual(offset, len(data))
            self.assertIn(path.relative_to(ROOT).as_posix(), rights["rights_cleared_for_release"])

    def test_privacy_and_discovery(self):
        text = PAGE.read_text("utf-8") + (PAGE.parent / "article.css").read_text("utf-8")
        for pattern in (
            r"(?i)(?<![a-z])[a-z]:[\\/]|file://|ssh://",
            r"(?i)localhost|127\.0\.0\.1|/workspace/|/home/|\.zip|\.yaml|\.step|GX10",
            r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b",
        ):
            self.assertIsNone(re.search(pattern, text))
        for name in ("publishing/index.html", "publishing/robotic-hand/index.html",
                     "publishing/studies/index.html", "publishing/llms.txt",
                     "publishing/sitemap.xml", "scripts/build-site.ps1"):
            self.assertIn(ROUTE, (ROOT / name).read_text("utf-8"))
        self.assertIn('rel="canonical" href="https://marb.cadclaw.io' + ROUTE + '"', text)


if __name__ == "__main__":
    unittest.main()
