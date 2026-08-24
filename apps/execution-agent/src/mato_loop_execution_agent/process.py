"""Compatibility alias for process and shell operations."""

import sys

from .operations import process as _implementation

sys.modules[__name__] = _implementation
