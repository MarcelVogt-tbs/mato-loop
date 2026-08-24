"""Compatibility alias for OpenSCAD operations."""

import sys

from .operations import openscad as _implementation

sys.modules[__name__] = _implementation
