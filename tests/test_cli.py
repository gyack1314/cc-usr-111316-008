"""CLI 与审计输出的冒烟测试。"""
import json
import tempfile
import unittest
from pathlib import Path

from analysis_data.cli import main


class CliTests(unittest.TestCase):
    def _run(self, *argv):
        with tempfile.TemporaryDirectory() as capture:
            import contextlib
            import io

            output = io.StringIO()
            error = io.StringIO()
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
                code = main(list(argv))
            return code, output.getvalue(), error.getvalue()

    def test_index_command_emits_scope_and_method(self):
        code, stdout, _ = self._run("index", "data/normal.json", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertEqual(payload["method"], "geks-fisher")
        self.assertIn("comparison_scope", payload)
        self.assertIn("edge_matrix", payload)

    def test_graph_command_marks_incomplete(self):
        code, stdout, _ = self._run("index", "data/disconnected-products.json", "--json")
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertFalse(payload["complete_graph"])
        self.assertEqual(payload["method"], "weighted-graph-least-squares")
        self.assertIsNone(payload["index"]["2026-03"])

    def test_rolling_failure_exits_nonzero(self):
        code, _, stderr = self._run("rolling", "data/disconnected-products.json")
        self.assertEqual(code, 2)
        self.assertIn("阻断", stderr)

    def test_invalid_config_exits_nonzero(self):
        code, _, stderr = self._run("inspect", "data/invalid-config.json")
        self.assertEqual(code, 2)
        self.assertIn("window", stderr)

    def test_analyze_no_rolling_skips_link(self):
        code, stdout, _ = self._run("analyze", "data/normal.json", "--json", "--no-rolling")
        self.assertEqual(code, 0)
        payload = json.loads(stdout)
        self.assertNotIn("rolling", payload)


if __name__ == "__main__":
    unittest.main()
