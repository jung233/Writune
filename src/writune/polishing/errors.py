"""Errors raised by polishing safeguards."""


class ValidationError(ValueError):
    """A deterministic output constraint was not satisfied."""

    def __init__(self, message: str, *, code: str = "VALIDATION_FAILED") -> None:
        super().__init__(message)
        self.code = code
