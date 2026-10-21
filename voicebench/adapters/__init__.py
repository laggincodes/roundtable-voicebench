"""Target Adapter exports for VoiceBench."""

from voicebench.adapters.base import TargetAdapter
from voicebench.adapters.mock import LocalMockAdapter

__all__ = ["TargetAdapter", "LocalMockAdapter"]
