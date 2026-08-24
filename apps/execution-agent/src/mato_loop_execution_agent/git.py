"""Compatibility alias for Git operations."""

import sys

from .operations import git as _implementation

sys.modules[__name__] = _implementation
