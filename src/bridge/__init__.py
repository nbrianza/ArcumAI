# Re-export bridge_manager so all existing call sites
# (from src.bridge import bridge_manager) continue to work unchanged.
from src.bridge.manager import bridge_manager

__all__ = ["bridge_manager"]
