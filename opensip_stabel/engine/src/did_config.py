#!/usr/bin/env python
"""
DID-based configuration loader for multi-tenant support.
Loads JSON configuration files based on the DID (Direct Inward Dialing) number.
Each restaurant/tenant can have its own configuration file named after the DID number.

Can optionally merge with database configs from Django backend via HTTP API.
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Optional, Any
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


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
        
        # If path is relative, try to resolve it relative to the engine directory
        if not self.config_dir.is_absolute():
            # Try to find the engine directory (where this file is located)
            # This file is in: engine/src/did_config.py
            # Config should be in: engine/config/did/
            current_file = Path(__file__).resolve()
            # Go up from src/ to engine/, then to config/did/
            engine_dir = current_file.parent.parent  # engine/src -> engine/
            potential_config_dir = engine_dir / 'config' / 'did'
            
            # If the relative path doesn't exist, try the engine-relative path
            if not self.config_dir.exists() and potential_config_dir.exists():
                self.config_dir = potential_config_dir
                logging.info("Using engine-relative config directory: %s", self.config_dir)
            else:
                # Make it absolute based on current working directory
                self.config_dir = self.config_dir.resolve()
        
        # Ensure directory exists
        self.config_dir.mkdir(parents=True, exist_ok=True)
        
        # Cache for loaded configurations
        self._config_cache: Dict[str, Dict[str, Any]] = {}
        
        logging.info("DID Config Loader initialized: %s (absolute: %s)", self.config_dir, self.config_dir.is_absolute())
    
    def _normalize_did(self, did: str) -> str:
        """
        Normalize DID number for filename matching.
        Removes common SIP URI prefixes, country codes, and special characters.
        
        Args:
            did: DID number (e.g., "09154211914", "sip:09154211914@domain.com", "985191096575")
            
        Returns:
            Normalized DID number (e.g., "09154211914", "5191096575")
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
        
        # Remove country code 98 (Iran) if present at the start
        # e.g., "985191096575" -> "5191096575"
        if normalized.startswith("98") and len(normalized) > 2:
            normalized = normalized[2:]
        
        return normalized
    
    def _generate_did_variations(self, did: str) -> list:
        """
        Generate all possible variations of a DID number for matching.
        
        Args:
            did: DID number (e.g., "985191096575", "5191096575")
            
        Returns:
            List of DID variations to try (in order of preference)
        """
        variations = []
        
        # Start with original
        variations.append(did)
        
        # Normalize (removes country code, etc.)
        normalized = self._normalize_did(did)
        if normalized != did:
            variations.append(normalized)
        
        # Try with leading 0 if it doesn't have one
        if normalized and not normalized.startswith("0") and len(normalized) >= 9:
            with_zero = "0" + normalized
            if with_zero not in variations:
                variations.append(with_zero)
        
        # Try without leading 0 if it has one
        if normalized and normalized.startswith("0") and len(normalized) > 1:
            without_zero = normalized[1:]
            if without_zero not in variations:
                variations.append(without_zero)
        
        return variations
    
    def _find_config_file(self, did: str) -> Optional[Path]:
        """
        Find configuration file for a given DID number.
        Tries multiple naming patterns:
        1. All DID variations (with/without country code, with/without leading 0)
        2. default.json (fallback)
        
        Args:
            did: DID number (the destination number being called)
            
        Returns:
            Path to config file or None if not found
        """
        logging.info("🔍 Searching for DID config file:")
        logging.info("   Original DID: %s", did)
        logging.info("   Config directory: %s", self.config_dir)
        
        # Generate all possible variations
        variations = self._generate_did_variations(did)
        logging.info("   DID variations to try: %s", variations)
        
        # List available config files for debugging
        available_files = list(self.config_dir.glob("*.json"))
        if available_files:
            logging.info("   Available config files: %s", [f.name for f in available_files])
        else:
            logging.warning("   No JSON config files found in %s", self.config_dir)
        
        # Try each variation in order
        for variation in variations:
            config_path = self.config_dir / f"{variation}.json"
            logging.info("   Trying: %s (exists: %s)", config_path.name, config_path.exists())
            if config_path.exists():
                logging.info("✅ Found match: %s", config_path.name)
                return config_path
        
        # Try default fallback
        default_path = self.config_dir / "default.json"
        logging.info("   Trying default: %s (exists: %s)", default_path.name, default_path.exists())
        if default_path.exists():
            logging.warning("⚠️  Using default.json for DID: %s (no specific config found)", did)
            return default_path
        
        logging.error("❌ No config file found (not even default.json)")
        return None
    
    def load_config(self, did: str, merge_with_db: bool = False, backend_url: str = None) -> Dict[str, Any]:
        """
        Load configuration for a specific DID number.
        Uses cache to avoid reloading files.
        Optionally merges with database config from Django backend.
        
        Args:
            did: DID number (the number being called)
            merge_with_db: If True, attempt to merge with database config from backend
            backend_url: Backend URL for fetching database config (optional)
            
        Returns:
            Dictionary containing configuration, or empty dict if not found
        """
        if not did:
            logging.warning("DID Config: No DID provided, using default")
            return self._load_default_config()
        
        # Check cache first
        cache_key = f"{did}:{merge_with_db}"
        if cache_key in self._config_cache:
            logging.debug("DID Config: Using cached config for %s", did)
            return self._config_cache[cache_key]
        
        # Find and load config file
        config_file = self._find_config_file(did)
        
        json_config = {}
        if config_file:
            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    json_config = json.load(f)
                
                # Validate config structure
                if not isinstance(json_config, dict):
                    logging.error("DID Config: Invalid config file format for %s", did)
                    json_config = {}
                else:
                    logging.info("DID Config: Loaded JSON config for DID %s from %s", did, config_file.name)
            except json.JSONDecodeError as e:
                logging.error("DID Config: JSON decode error for %s: %s", did, e)
            except Exception as e:
                logging.error("DID Config: Error loading config file for %s: %s", did, e)
        
        # If no JSON config found, try default
        if not json_config:
            json_config = self._load_default_config()
        
        # Optionally merge with database config
        if merge_with_db and HAS_REQUESTS:
            db_config = self._load_db_config(did, backend_url)
            if db_config:
                # Database config takes precedence over JSON
                merged = json_config.copy()
                merged.update(db_config)
                json_config = merged
                logging.info("DID Config: Merged with database config for DID %s", did)
        
        # Cache the final config
        self._config_cache[cache_key] = json_config
        
        return json_config
    
    def _load_db_config(self, did: str, backend_url: str = None) -> Optional[Dict[str, Any]]:
        """
        Load tenant configuration from Django backend via HTTP API.
        
        Args:
            did: DID number
            backend_url: Backend URL (if None, tries to get from config or env)
            
        Returns:
            Database config dictionary or None
        """
        if not HAS_REQUESTS:
            logging.debug("DID Config: requests library not available, skipping DB config")
            return None
        
        if not backend_url:
            backend_url = os.getenv('BACKEND_SERVER_URL', 'http://backend-restaurant:8000')
        
        normalized_did = self._normalize_did(did)
        
        try:
            api_url = f"{backend_url.rstrip('/')}/api/tenant-config/{normalized_did}/"
            response = requests.get(api_url, timeout=2)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                logging.debug("DID Config: No database config found for DID %s", did)
            else:
                logging.warning("DID Config: Backend returned status %d for DID %s", response.status_code, did)
        except requests.exceptions.RequestException as e:
            logging.debug("DID Config: Could not fetch DB config for DID %s: %s", did, e)
        except Exception as e:
            logging.warning("DID Config: Error loading DB config for DID %s: %s", did, e)
        
        return None
    
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

