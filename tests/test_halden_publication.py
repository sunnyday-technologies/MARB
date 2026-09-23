"""Exercise the narrow executable-module exception in the actual release gate."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PWSH = shutil.which('pwsh') or os.environ.get('MARB_TEST_PWSH')
PAGE = 'publishing/robotic-hand/halden-mkiii/index.html'
MODULE = 'publishing/robotic-hand/halden-mkiii/assets/halden-27c86ba84a86.js'


@unittest.skipUnless(PWSH, 'PowerShell 7 required for release-gate integration')
class HaldenReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / 'publishing', self.root / 'publishing')
        for name in ('scripts/build-site.ps1', 'publication-rights.json',
                     'results/marb_runs.json', 'spec/MARB_SCORING.md'):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)

    def change(self, name, before, after):
        path = self.root / name
        text = path.read_text(encoding='utf-8')
        self.assertIn(before, text)
        path.write_text(text.replace(before, after), encoding='utf-8')

    def gate(self, error=None):
        result = subprocess.run([PWSH, '-NoProfile', '-File',
                                 str(self.root / 'scripts/build-site.ps1'), '-Release'],
                                capture_output=True, text=True, timeout=90)
        output = result.stdout + result.stderr
        if error:
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn(error, output)
        else:
            self.assertEqual(result.returncode, 0, output)

    def test_approved_release(self):
        self.gate()

    def test_modified_bundle_is_rejected(self):
        with (self.root / MODULE).open('ab') as file:
            file.write(b'\n/* unreviewed change */')
        self.gate('Unreviewed Halden module bytes')

    def test_missing_integrity_is_rejected(self):
        self.change(PAGE, 'integrity="sha256-', 'data-hash="sha256-')
        self.gate('Halden must load exactly one reviewed module with SRI')

    def test_additional_script_is_rejected(self):
        self.change(PAGE, '</head>', '<script src="https://example.com/unreviewed.js"></script></head>')
        self.gate('Unapproved executable script')

    def test_module_on_other_route_is_rejected(self):
        self.change('publishing/robotic-hand/index.html', '</head>',
                    '<script src="/robotic-hand/halden-mkiii/assets/halden-27c86ba84a86.js"></script></head>')
        self.gate('Unapproved executable script')

    def test_broad_csp_allowance_is_rejected(self):
        self.change('publishing/_headers', 'script-src ', "script-src 'self' ")
        self.gate('CSP script-src must contain only the reviewed hashes')


if __name__ == '__main__':
    unittest.main()
