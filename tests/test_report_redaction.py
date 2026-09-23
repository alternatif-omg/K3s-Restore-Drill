from __future__ import annotations

import unittest

from k3s_restore_drill.report import redact, redact_data


class ReportRedactionTests(unittest.TestCase):
    def test_known_token_and_key_value_secrets_are_redacted(self) -> None:
        value = redact("token=abc123 failed with abc123", ("abc123",))
        self.assertNotIn("abc123", value)
        self.assertIn("[REDACTED]", value)

    def test_secret_named_fields_are_removed_recursively(self) -> None:
        report = redact_data({"token": "abc", "stages": [{"password": "def"}], "status": "PASS"})
        self.assertEqual(report, {"token": "[REDACTED]", "stages": [{"password": "[REDACTED]"}], "status": "PASS"})


if __name__ == "__main__":
    unittest.main()
