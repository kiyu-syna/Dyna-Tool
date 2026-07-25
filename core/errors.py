from __future__ import annotations


class DynaToolError(RuntimeError):
    """Base operational error with stable retry semantics for workers and UI."""

    code = "dyna_error"
    retryable = False

    def __init__(self, message: str, *, cause: BaseException | None = None):
        super().__init__(message)
        self.cause = cause


class BrowserUnavailableError(DynaToolError):
    code = "browser_unavailable"
    retryable = True


class ConfigurationError(DynaToolError):
    code = "configuration_error"


def is_retryable_error(error: BaseException) -> bool:
    """Central retry policy; unknown errors are deliberately non-retryable."""
    return isinstance(error, DynaToolError) and error.retryable
