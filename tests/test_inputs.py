import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import compare_chapters as c

class InputTests(unittest.TestCase):
    def test_passage_sizes(self):
        self.assertEqual(c.PASSAGES['john1'], ('JHN', 1, 51))
        self.assertEqual(c.PASSAGES['romans8'], ('ROM', 8, 39))

    def test_private_tsv_loading(self):
        with tempfile.TemporaryDirectory() as td:
            base = Path(td)
            folder = base / 'translations' / 'private'
            folder.mkdir(parents=True)
            with (folder / 'ESV.tsv').open('w', encoding='utf-8', newline='') as f:
                w = csv.writer(f, delimiter='\t')
                w.writerow(['verse', 'text'])
                w.writerow(['JHN.1.1', 'synthetic test words'])
            with patch.object(c, 'ROOT', base):
                self.assertEqual(c.read_translation('ESV')['JHN.1.1'], 'synthetic test words')
                self.assertIsNone(c.read_translation('NWT'))

if __name__ == '__main__': unittest.main()
