"""Тесты взвешенного S, routing, времени, контекста и XLSX/TXT."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from example.models import Check, CheckResult, RunResult, TestCase
from example.report import write_results_xlsx
from example.scoring import calculate_one_number, write_one_number_result


def check_result(metric, success, weight=None, error=""):
    check = Check(text=metric, metric=metric, weight=weight)
    return CheckResult(
        check=check,
        success=success,
        error=error,
        artifact_path="result.custom",
        input_attribute="raw_structure",
    )


class ScoringTests(unittest.TestCase):
    def test_weighted_score_excludes_routing_errors_and_time(self):
        result = RunResult(
            test_case=TestCase(id="case", user_phrase="", skill="expected", artifact_type=".md"),
            actual_skill="other",
            routing_success=False,
            tools=["write", "read"],
            agent_time_sec=10,
            context_tokens=120568,
            context_limit=196608,
            context_percent=61,
            check_results=[
                check_result("Quality", True, 2),
                check_result("Quality", False, 2),
                check_result("Layout", True, 1),
                check_result("Broken", False, 5, error="judge error"),
            ],
        )
        summary = calculate_one_number([result])
        self.assertAlmostEqual(summary["score"], 2 / 3 * 0.5 + 1 / 3)
        self.assertNotIn("Broken", summary["metrics"])
        self.assertEqual(summary["routing_all"], 1)
        self.assertEqual(summary["routing_pass"], 0)
        self.assertEqual(summary["average_time"], 10)
        self.assertEqual(summary["max_context_tokens"], 120568)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text_path = Path(write_one_number_result([result], root / "one_number_result.txt"))
            text = text_path.read_text(encoding="utf-8")
            self.assertIn("S = 0.666667", text)
            self.assertIn("routing = 0/1 (в S не участвует)", text)
            self.assertIn("average_agent_time_sec = 10.000", text)
            self.assertIn("max_context_percent = 61.0", text)

            xlsx_path = Path(write_results_xlsx([result], root / "results.xlsx"))
            rows = list(load_workbook(xlsx_path, read_only=True).active.iter_rows(values_only=True))
            routing = next(row for row in rows if row[6] == "routing")
            self.assertEqual(routing[7], "FAILED")
            self.assertIn("other", routing[8])
            self.assertTrue(any(row[6] == "agent_time" and row[7] == "INFO" for row in rows))
            self.assertTrue(any(row[6] == "tools" and "write, read" in row[8] for row in rows))
            self.assertTrue(any(row[6] == "context" and "120568/196608" in row[8] for row in rows))
            self.assertTrue(any(row[12] == "result.custom" and row[13] == "raw_structure" for row in rows))

    def test_equal_weights_and_conflicts(self):
        result = RunResult(
            test_case=TestCase(id="case", user_phrase="", skill="", artifact_type=".md"),
            routing_applicable=False,
            check_results=[check_result("A", True), check_result("B", False)],
        )
        summary = calculate_one_number([result])
        self.assertEqual(summary["score"], 0.5)
        self.assertEqual(summary["metrics"]["A"]["normalized_weight"], 0.5)

        result.check_results.append(check_result("A", True, 3))
        with self.assertRaisesRegex(ValueError, "Конфликтующие веса"):
            calculate_one_number([result])


if __name__ == "__main__":
    unittest.main()
