"""
DEPRECATED: This file is kept for backward compatibility only.
Please use shared.config_settings.ConfigSettings instead.

This file will be removed in a future version.
"""

import warnings
warnings.warn(
    "Reservation_Module.config_settings is deprecated. "
    "Use shared.config_settings instead.",
    DeprecationWarning,
    stacklevel=2
)

# Re-export from shared to maintain backward compatibility
from shared.config_settings import ConfigSettings

__all__ = ['ConfigSettings']
