#!/usr/bin/env python3
"""Backend Manager Registry.

Provides registration and discovery of backend managers.
"""

from __future__ import annotations

from typing import Any

from .base import BackendManager, ManagerError

# Global registry of backend managers
_managers: dict[str, type[BackendManager]] = {}


def register_manager(manager_class: type[BackendManager]) -> type[BackendManager]:
    """Register a backend manager class.

    Args:
        manager_class: BackendManager subclass to register

    Returns:
        The same class (for decorator usage)

    Raises:
        ManagerError: If BACKEND_NAME is not set or already registered
    """
    if not manager_class.BACKEND_NAME:
        raise ManagerError(f"Backend manager {manager_class.__name__} has empty BACKEND_NAME")
    if manager_class.BACKEND_NAME in _managers:
        raise ManagerError(f"Backend manager already registered for: {manager_class.BACKEND_NAME}")
    _managers[manager_class.BACKEND_NAME] = manager_class
    return manager_class


def get_manager(name: str) -> type[BackendManager] | None:
    """Get a registered backend manager class by name.

    Args:
        name: Backend name (e.g., "rapid-mlx", "vllm-mlx")

    Returns:
        Manager class or None if not found
    """
    return _managers.get(name)


def get_manager_instance(name: str, home: Any = None) -> BackendManager | None:
    """Get an instance of a registered backend manager.

    Args:
        name: Backend name
        home: Optional home directory override

    Returns:
        Manager instance or None if not found
    """
    manager_class = get_manager(name)
    if manager_class is None:
        return None
    return manager_class(home)


def list_managers() -> list[str]:
    """Get list of registered backend names.

    Returns:
        Sorted list of backend names
    """
    return sorted(_managers.keys())


def iter_managers() -> list[tuple[str, type[BackendManager]]]:
    """Iterate over all registered managers.

    Returns:
        List of (name, class) tuples
    """
    return sorted(_managers.items())


__all__ = [
    "register_manager",
    "get_manager",
    "get_manager_instance",
    "list_managers",
    "iter_managers",
    "BackendManager",
    "ManagerError",
]