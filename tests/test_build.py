import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class BuildTests(unittest.TestCase):
    def test_build_keeps_fund_without_premium_and_handles_missing_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data, site = root / "data", root / "site"
            data.mkdir()
            (data / "latest.json").write_text(json.dumps({"rows": [{"code": "169201", "name": "A"}, {"code": "501018", "name": "B"}]}))
            (data / "index.json").write_text(json.dumps({"dates": []}))
            (data / "volume").mkdir()
            volume_path = data / "volume" / "latest.json"
            volume_path.write_text(json.dumps({"checked_through": "2026-09-30", "funds": {"169201": {"checked_through": "2026-09-30", "points": [["2026-09-30", 100, 200]]}}}))
            command = [sys.executable, "build_dashboard_data.py", "--data-dir", str(data), "--site-data-dir", str(site)]
            subprocess.run(command, check=True, capture_output=True)
            fund = json.loads((site / "trends" / "169201.json").read_text())
            self.assertEqual(fund["daily_volume"], [["2026-09-30", 100, 200]])
            self.assertEqual(fund["points"], [])
            self.assertTrue((site / "trends" / "501018.json").exists())
            volume_path.unlink()
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual(json.loads((site / "trends" / "169201.json").read_text())["daily_volume"], [])
