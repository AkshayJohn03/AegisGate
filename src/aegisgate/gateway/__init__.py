"""Gateway package: FastAPI app and metrics.

``app`` is imported lazily (``from aegisgate.gateway import create_app`` works
via module attribute access in ``__getattr__``) to avoid the circular import
``pipeline -> gateway.metrics -> gateway.__init__ -> app -> pipeline``.
"""

from typing import Any

from aegisgate.gateway.metrics import Metrics

__all__ = ["Metrics", "build_pipeline", "create_app"]


def __getattr__(name: str) -> Any:  # lazy app import
    if name in {"build_pipeline", "create_app"}:
        from aegisgate.gateway import app as app_module

        return getattr(app_module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
