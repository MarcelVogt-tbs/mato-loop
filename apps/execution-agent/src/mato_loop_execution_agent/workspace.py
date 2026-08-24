"""Compatibility alias for workspace operations."""

import sys

from .operations import workspace as _implementation

sys.modules[__name__] = _implementation
