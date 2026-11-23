"""Adapter package: platform-specific extractors behind one interface."""

from mediavault.adapters.base import Adapter, AdapterError, AdapterStale
from mediavault.adapters.registry import AdapterRegistry, build_default_registry

__all__ = [
    "Adapter",
    "AdapterError",
    "AdapterStale",
    "AdapterRegistry",
    "build_default_registry",
]
