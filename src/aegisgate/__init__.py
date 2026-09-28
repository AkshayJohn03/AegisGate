"""AegisGate: self-healing LLM gateway and control plane."""

from aegisgate.clock import Clock, ManualClock, SystemClock

__version__ = "0.1.0"

__all__ = ["Clock", "ManualClock", "SystemClock", "__version__"]
