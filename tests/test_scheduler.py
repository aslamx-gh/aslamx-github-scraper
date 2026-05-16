"""Tests for scheduler: cron validation."""

import unittest
from src.services.scheduler import validate_cron


class TestCronValidation(unittest.TestCase):
    def test_valid_cron(self):
        valid, err = validate_cron("0 2 * * *")
        self.assertTrue(valid)
        self.assertIsNone(err)

    def test_valid_every_minute(self):
        valid, err = validate_cron("* * * * *")
        self.assertTrue(valid)

    def test_valid_weekly(self):
        valid, err = validate_cron("0 0 * * 1")
        self.assertTrue(valid)

    def test_invalid_cron(self):
        valid, err = validate_cron("not a cron")
        self.assertFalse(valid)
        self.assertIsNotNone(err)

    def test_invalid_too_many_fields(self):
        valid, err = validate_cron("0 0 0 0 0 0 0")
        self.assertFalse(valid)

    def test_empty_string(self):
        valid, err = validate_cron("")
        self.assertFalse(valid)


if __name__ == "__main__":
    unittest.main()
