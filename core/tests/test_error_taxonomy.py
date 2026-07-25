import unittest

from core.errors import (
    BrowserUnavailableError,
    ConfigurationError,
    is_retryable_error,
)


class ErrorTaxonomyTests(unittest.TestCase):
    def test_only_transient_operational_errors_are_retryable(self):
        self.assertTrue(is_retryable_error(BrowserUnavailableError("offline")))
        self.assertFalse(is_retryable_error(ConfigurationError("invalid")))
        self.assertFalse(is_retryable_error(RuntimeError("unknown")))
