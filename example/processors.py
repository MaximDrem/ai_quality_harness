"""Совместимый re-export фабрики процессоров из checks/processors."""

from .checks.processors import clear_processor_cache, get_processor

__all__ = ["clear_processor_cache", "get_processor"]
