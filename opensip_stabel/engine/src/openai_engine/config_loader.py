"""
Configuration loader for OpenAI engine - handles DID config loading and merging.
"""

import logging
from did_config import load_did_config
from config import Config

logger = logging.getLogger(__name__)


class ConfigLoader:
    """Handles loading and merging of DID configurations"""
    
    @staticmethod
    def load_did_config_for_call(call, cfg):
        """
        Load DID configuration for a call, trying original DID first, then current DID, then default.
        
        Args:
            call: Call object with did_number and original_did_number
            cfg: Base configuration object
            
        Returns:
            Tuple of (did_config, did_number, backend_url)
        """
        did_number = getattr(call, 'did_number', None)
        original_did_number = getattr(call, 'original_did_number', None)
        
        logger.info("🔍 Loading DID config - did_number: %s, original_did_number: %s", 
                   did_number, original_did_number)
        
        did_config = None
        tried_dids = []
        config_source = None
        
        # Priority 1: Original DID (what the user actually called)
        if original_did_number and original_did_number != did_number:
            tried_dids.append(f"{original_did_number} (original)")
            original_config = load_did_config(original_did_number)
            if original_config:
                description = original_config.get('description', 'Unknown')
                service_name = original_config.get('service_name') or original_config.get('restaurant_name', 'Unknown')
                logger.info("✅ DID config loaded from original DID %s: %s (service: %s, IVR routed to: %s)", 
                           original_did_number, description, service_name, did_number)
                did_config = original_config
                config_source = f"original DID {original_did_number}"
        
        # Priority 2: Current DID (IVR selection)
        if not did_config:
            if did_number:
                tried_dids.append(f"{did_number} (current)")
                current_config = load_did_config(did_number)
                if current_config:
                    description = current_config.get('description', 'Unknown')
                    service_name = current_config.get('service_name') or current_config.get('restaurant_name', 'Unknown')
                    logger.info("✅ DID config loaded for current DID %s: %s (service: %s)", 
                               did_number, description, service_name)
                    did_config = current_config
                    config_source = f"current DID {did_number}"
        
        # Priority 3: Fallback to default
        if not did_config:
            logger.warning("⚠️  No DID config found (tried: %s), using default", 
                          ", ".join(tried_dids) if tried_dids else "none")
            default_config = load_did_config("default")
            if default_config:
                logger.info("✅ Using default DID config")
                did_config = default_config
                config_source = "default"
            else:
                logger.error("❌ No default DID config found either! Call may fail.")
                did_config = {}
                config_source = "none (empty config)"
        
        if did_config and config_source:
            logger.info("📋 DID config summary: source=%s, service=%s, backend_url=%s",
                       config_source,
                       did_config.get('service_name') or did_config.get('restaurant_name', 'N/A'),
                       did_config.get('backend_url', 'N/A'))
        
        # Get backend URL with proper resolution
        backend_url = ConfigLoader.resolve_backend_url(did_config)
        
        return did_config, did_number, backend_url
    
    @staticmethod
    def resolve_backend_url(did_config: dict = None) -> str:
        """
        Resolve backend URL with proper priority:
        1. DID config backend_url (if valid and not localhost in production)
        2. BACKEND_SERVER_URL environment variable
        3. Default to backend-restaurant:8000 (Docker service name)
        
        Args:
            did_config: Optional DID config dictionary
            
        Returns:
            Resolved backend URL string
        """
        import os
        
        # Priority 1: DID config backend_url
        did_backend_url = None
        if did_config:
            did_backend_url = did_config.get('backend_url')
        
        # Priority 2: Environment variable
        env_backend_url = os.getenv('BACKEND_SERVER_URL')
        
        # Priority 3: Default (Docker service name)
        default_backend_url = "http://backend-restaurant:8000"
        
        # Determine which URL to use
        environment = os.getenv('ENVIRONMENT', 'production')
        
        # If DID config has backend_url, validate it
        if did_backend_url:
            # In production, reject localhost URLs
            if environment == 'production' and ('localhost' in did_backend_url or '127.0.0.1' in did_backend_url):
                logger.error("❌ Invalid backend_url in DID config: %s (localhost not allowed in production)", 
                           did_backend_url)
                logger.info("   Falling back to BACKEND_SERVER_URL or default")
                did_backend_url = None
        
        # Use DID config URL if valid
        if did_backend_url and did_backend_url.strip():
            logger.info("✅ Using backend_url from DID config: %s", did_backend_url)
            return did_backend_url.strip()
        
        # Use environment variable if set
        if env_backend_url and env_backend_url.strip():
            logger.info("✅ Using backend_url from BACKEND_SERVER_URL env: %s", env_backend_url)
            return env_backend_url.strip()
        
        # Use default Docker service name
        logger.info("✅ Using default backend_url (Docker service): %s", default_backend_url)
        return default_backend_url
    
    @staticmethod
    def merge_openai_config(base_cfg, did_config):
        """
        Merge base OpenAI config with DID-specific overrides.
        
        Args:
            base_cfg: Base configuration section
            did_config: DID-specific configuration dict
            
        Returns:
            MergedConfigSection object
        """
        merged_cfg_dict = dict(base_cfg)
        if did_config:
            if 'openai' in did_config:
                merged_cfg_dict.update(did_config['openai'])
            # Also check top-level keys
            for key in ['model', 'voice', 'temperature', 'welcome_message', 'intro']:
                if key in did_config:
                    merged_cfg_dict[key] = did_config[key]
        
        class MergedConfigSection:
            def __init__(self, base_section, did_overrides):
                self._base = base_section
                self._overrides = did_overrides
                
            def get(self, option, env=None, fallback=None):
                if isinstance(option, list):
                    for opt in option:
                        if opt in self._overrides:
                            return self._overrides[opt]
                    return self._base.get(option, env, fallback)
                else:
                    if option in self._overrides:
                        return self._overrides[option]
                    return self._base.get(option, env, fallback)
            
            def getboolean(self, option, env=None, fallback=None):
                val = self.get(option, env, None)
                if val is None:
                    return fallback
                if isinstance(val, bool):
                    return val
                if isinstance(val, str):
                    if val.isnumeric():
                        return int(val) != 0
                    if val.lower() in ["yes", "true", "on"]:
                        return True
                    if val.lower() in ["no", "false", "off"]:
                        return False
                return fallback
        
        return MergedConfigSection(base_cfg, merged_cfg_dict)
    
    @staticmethod
    def merge_soniox_config(base_cfg, did_config):
        """
        Merge base Soniox config with DID-specific overrides.
        
        Args:
            base_cfg: Base Soniox configuration section
            did_config: DID-specific configuration dict
            
        Returns:
            MergedSonioxConfig object
        """
        soniox_overrides = {}
        if did_config and 'soniox' in did_config:
            soniox_overrides = did_config['soniox']
        
        class MergedSonioxConfig:
            def __init__(self, base, overrides):
                self._base = base
                self._overrides = overrides
                
            def get(self, option, env=None, fallback=None):
                if option in self._overrides:
                    return self._overrides[option]
                return self._base.get(option, env, fallback)
            
            def getboolean(self, option, env=None, fallback=None):
                val = self.get(option, env, None)
                if val is None:
                    return fallback
                if isinstance(val, bool):
                    return val
                if isinstance(val, str):
                    if val.isnumeric():
                        return int(val) != 0
                    if val.lower() in ["yes", "true", "on"]:
                        return True
                    if val.lower() in ["no", "false", "off"]:
                        return False
                return fallback
        
        return MergedSonioxConfig(base_cfg, soniox_overrides)
    
    @staticmethod
    def get_welcome_message(did_config, cfg):
        """Get welcome message from DID config or fallback to base config"""
        if did_config:
            welcome = (did_config.get('welcome_message') or 
                      did_config.get('intro') or
                      (did_config.get('openai', {}).get('welcome_message') if isinstance(did_config.get('openai'), dict) else None) or
                      (did_config.get('openai', {}).get('intro') if isinstance(did_config.get('openai'), dict) else None))
            if welcome:
                return welcome
        return cfg.get("welcome_message", "OPENAI_WELCOME_MESSAGE", "")
