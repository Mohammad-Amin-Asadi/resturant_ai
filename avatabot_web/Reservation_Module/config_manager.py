"""
DEPRECATED: This file is kept for backward compatibility only.
Please use shared.config_manager.ConfigManager instead.

This file will be removed in a future version.
"""

import warnings
warnings.warn(
    "Reservation_Module.config_manager is deprecated. "
    "Use shared.config_manager instead.",
    DeprecationWarning,
    stacklevel=2
)

# Re-export from shared to maintain backward compatibility
from shared.config_manager import ConfigManager

__all__ = ['ConfigManager']
