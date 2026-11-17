import importlib.util
import os
import unittest
from datetime import date, timedelta

os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("COST_EXPLORER_REGION", "us-east-1")
spec = importlib.util.spec_from_file_location("handler", "lambda/handler.py")
handler = importlib.util.module_from_spec(spec)
spec.loader.exec_module(handler)


class CostAnalysisTests(unittest.TestCase):
    def test_detects_spend_spike(self):
        today = date.today()
        costs = {(today - timedelta(days=1)).isoformat(): 130.0}
        for offset in range(2, 9):
            costs[(today - timedelta(days=offset)).isoformat()] = 100.0
        result = handler.analyze_cost(costs)
        self.assertTrue(result["is_anomaly"])
        self.assertEqual(result["change_percent"], 30.0)

    def test_no_anomaly_under_threshold(self):
        today = date.today()
        costs = {(today - timedelta(days=1)).isoformat(): 110.0}
        for offset in range(2, 9):
            costs[(today - timedelta(days=offset)).isoformat()] = 100.0
        result = handler.analyze_cost(costs)
        self.assertFalse(result["is_anomaly"])

    def test_zero_baseline_is_safe(self):
        today = date.today()
        costs = {(today - timedelta(days=1)).isoformat(): 12.0}
        for offset in range(2, 9):
            costs[(today - timedelta(days=offset)).isoformat()] = 0.0
        result = handler.analyze_cost(costs)
        self.assertFalse(result["is_anomaly"])
        self.assertIsNone(result["change_percent"])


if __name__ == "__main__":
    unittest.main()
