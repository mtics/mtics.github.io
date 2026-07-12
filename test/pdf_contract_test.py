import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CV_PDF = ROOT / "assets" / "pdf" / "cv.pdf"


class CvPdfContractTest(unittest.TestCase):
    def test_cv_pdf_has_valid_structure_and_descriptive_metadata(self):
        result = subprocess.run(
            ["pdfinfo", str(CV_PDF)],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Syntax Error", result.stderr)

        metadata = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition(":")
            if separator:
                metadata[key.strip()] = value.strip()

        self.assertEqual(metadata.get("Title"), "Zhiwei Li - Curriculum Vitae")
        self.assertEqual(metadata.get("Author"), "Zhiwei Li")
        self.assertEqual(metadata.get("Subject"), "Academic curriculum vitae")
        self.assertEqual(
            metadata.get("Keywords"),
            "Zhiwei Li, curriculum vitae, academic profile",
        )
        self.assertEqual(metadata.get("PDF version"), "1.5")

        text_result = subprocess.run(
            ["pdftotext", str(CV_PDF), "-"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(text_result.returncode, 0, text_result.stderr)
        self.assertNotIn("Syntax Error", text_result.stderr)
        self.assertIn("Zhiwei Li", text_result.stdout)


if __name__ == "__main__":
    unittest.main()
