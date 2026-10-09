"""Standalone MatoLoop Agent."""

from matoloop_version import get_version

from .config import AgentConfig
from .connection import MatoLoopAgent

__all__ = ["AgentConfig", "MatoLoopAgent"]
__version__ = get_version()
