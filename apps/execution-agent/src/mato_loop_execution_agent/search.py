"""Compatibility alias for search operations."""

import sys

from .operations import search as _implementation

sys.modules[__name__] = _implementation
