"""Compatibility re-exports for callers using the plural module name."""

from .prompt import Prompt, SCENES, STYLES, build_prompt

__all__ = ["Prompt", "SCENES", "STYLES", "build_prompt"]
