#!/usr/bin/env python
"""
DID-based configuration loader for multi-tenant support.
Loads JSON configuration files based on the DID (Direct Inward Dialing) number.
Each restaurant/tenant can have its own configuration file named after the DID number.
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Optional, Any


class DIDConfigLoader:
    """Loads and manages DID-specific configurations from JSON files."""
    
    def __init__(self, config_dir: str = None):
        """
        Initialize the DID configuration loader.
        
        Args:
            config_dir: Directory containing DID configuration JSON files.
                       Defaults to ./config/did/ or from DID_CONFIG_DIR env var.
        """
        if config_dir:
            self.config_dir = Path(config_dir)
        else:
            # Default to ./config/did/ or from environment
            default_dir = os.getenv('DID_CONFIG_DIR', './config/did/')
            self.config_dir = Path(default_dir)
        
        # Ensure directory exists
        self.config_dir.mkdir(parents=True, exist_ok=True)
        
        # Cache for loaded configurations
        self._config_cache: Dict[str, Dict[str, Any]] = {}
        
        logging.info("DID Config Loader initialized: %s", self.config_dir)
    
    def _normalize_did(self, did: str) -> str:
        """
        Normalize DID number for filename matching.
        Removes common SIP URI prefixes and special characters.
        
        Args:
            did: DID number (e.g., "09154211914", "sip:09154211914@domain.com")
            
        Returns:
            Normalized DID number (e.g., "09154211914")
        """
        if not did:
            return ""
        
        # Remove SIP URI prefix if present
        did = did.replace("sip:", "").replace("tel:", "")
        
        # Extract number from URI (e.g., "09154211914@domain.com" -> "09154211914")
        if "@" in did:
            did = did.split("@")[0]
        
        # Remove any non-digit characters except + at the start
        if did.startswith("+"):
            normalized = "+" + "".join(c for c in did[1:] if c.isdigit())
        else:
            normalized = "".join(c for c in did if c.isdigit())
        
        return normalized
    
    def _find_config_file(self, did: str) -> Optional[Path]:
        """
        Find configuration file for a given DID number.
        Tries multiple naming patterns:
        1. {did}.json (exact match)
        2. {normalized_did}.json (normalized)
        3. default.json (fallback)
        
        Args:
            did: DID number
            
        Returns:
            Path to config file or None if not found
        """
        normalized_did = self._normalize_did(did)
        
        # Try exact DID match
        exact_path = self.config_dir / f"{did}.json"
        if exact_path.exists():
            return exact_path
        
        # Try normalized DID match
        if normalized_did and normalized_did != did:
            normalized_path = self.config_dir / f"{normalized_did}.json"
            if normalized_path.exists():
                return normalized_path
        
        # Try default fallback
        default_path = self.config_dir / "default.json"
        if default_path.exists():
            logging.info("Using default.json for DID: %s", did)
            return default_path
        
        return None
    
    def load_config(self, did: str) -> Dict[str, Any]:
        """
        Load configuration for a specific DID number.
        Uses cache to avoid reloading files.
        
        Args:
            did: DID number (the number being called)
            
        Returns:
            Dictionary containing configuration, or empty dict if not found
        """
        if not did:
            logging.warning("DID Config: No DID provided, using default")
            return self._load_default_config()
        
        # Check cache first
        if did in self._config_cache:
            logging.debug("DID Config: Using cached config for %s", did)
            return self._config_cache[did]
        
        # Find and load config file
        config_file = self._find_config_file(did)
        
        if not config_file:
            logging.warning("DID Config: No config file found for DID: %s, using default", did)
            return self._load_default_config()
        
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # Validate config structure
            if not isinstance(config, dict):
                logging.error("DID Config: Invalid config file format for %s", did)
                return self._load_default_config()
            
            # Cache the config
            self._config_cache[did] = config
            
            logging.info("DID Config: Loaded config for DID %s from %s", did, config_file.name)
            return config
            
        except json.JSONDecodeError as e:
            logging.error("DID Config: JSON decode error for %s: %s", did, e)
            return self._load_default_config()
        except Exception as e:
            logging.error("DID Config: Error loading config for %s: %s", did, e)
            return self._load_default_config()
    
    def _load_default_config(self) -> Dict[str, Any]:
        """Load default configuration or return empty dict."""
        default_file = self.config_dir / "default.json"
        if default_file.exists():
            try:
                with open(default_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logging.error("DID Config: Error loading default.json: %s", e)
        return {}
    
    def get_config_value(self, did: str, key: str, default: Any = None) -> Any:
        """
        Get a specific configuration value for a DID.
        
        Args:
            did: DID number
            key: Configuration key (supports dot notation, e.g., "openai.voice")
            default: Default value if key not found
            
        Returns:
            Configuration value or default
        """
        config = self.load_config(did)
        
        # Support dot notation for nested keys
        keys = key.split('.')
        value = config
        
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        
        return value if value is not None else default
    
    def clear_cache(self):
        """Clear the configuration cache (useful for reloading configs)."""
        self._config_cache.clear()
        logging.info("DID Config: Cache cleared")


# Global instance
_did_config_loader: Optional[DIDConfigLoader] = None


def get_did_config_loader() -> DIDConfigLoader:
    """Get or create the global DID config loader instance."""
    global _did_config_loader
    if _did_config_loader is None:
        _did_config_loader = DIDConfigLoader()
    return _did_config_loader


def load_did_config(did: str) -> Dict[str, Any]:
    """
    Convenience function to load DID configuration.
    
    Args:
        did: DID number
        
    Returns:
        Configuration dictionary
    """
    loader = get_did_config_loader()
    return loader.load_config(did)

