"""Compatibility exports for output validation."""

from .errors import ValidationError
from .validate import validate

__all__ = ["ValidationError", "validate"]
