# DID-Based Configuration System

This directory contains JSON configuration files for different DID (Direct Inward Dialing) numbers. Each restaurant/tenant can have its own configuration file.

## File Naming Convention

Configuration files should be named after the DID number:
- `09154211914.json` - Configuration for DID 09154211914
- `02112345678.json` - Configuration for DID 02112345678
- `default.json` - Default configuration (used when no DID-specific config is found)

## Configuration Structure

Each JSON file should follow this structure:

```json
{
  "restaurant_name": "نام رستوران",
  "restaurant_id": "unique_id",
  "description": "Description of this configuration",
  
  "backend_url": "http://your-backend-server:8000",
  
  "openai": {
    "model": "gpt-realtime-2025-08-28",
    "voice": "alloy",
    "temperature": 0.8,
    "welcome_message": "پیام خوش‌آمدگویی",
    "intro": "متن معرفی"
  },
  
  "soniox": {
    "enabled": true,
    "model": "stt-rt-preview",
    "language_hints": ["fa"],
    "enable_speaker_diarization": false,
    "enable_language_identification": true,
    "enable_endpoint_detection": true,
    "upsample_audio": true,
    "silence_duration_ms": 500
  },
  
  "instructions_base": "دستورالعمل‌های پایه برای AI",
  
  "custom_context": {
    "menu_items": ["لیست", "غذاها"],
    "special_offers": "پیشنهادات ویژه"
  }
}
```

## Configuration Options

### Top-Level Options

- `restaurant_name`: Display name of the restaurant
- `restaurant_id`: Unique identifier for the restaurant
- `description`: Description of this configuration
- `backend_url`: Backend API URL (can be different per restaurant)

### OpenAI Section

All options from the base `config.ini` `[openai]` section can be overridden:
- `model`: OpenAI model to use
- `voice`: Voice for TTS (alloy, echo, fable, onyx, nova, shimmer)
- `temperature`: AI temperature (0.0-2.0)
- `welcome_message`: Initial greeting message
- `intro`: Introduction text

### Soniox Section

All options from the base `config.ini` `[soniox]` section can be overridden:
- `enabled`: Enable/disable Soniox STT
- `model`: Soniox model name
- `language_hints`: List of language codes (e.g., ["fa"])
- `enable_speaker_diarization`: Enable speaker diarization
- `enable_language_identification`: Enable language ID
- `enable_endpoint_detection`: Enable endpoint detection
- `upsample_audio`: Upsample audio for better quality
- `silence_duration_ms`: Silence duration before flushing transcript

### Custom Context

- `instructions_base`: Base instructions for the AI agent
- `custom_context`: Any custom data for the restaurant (menu items, special offers, etc.)

## How It Works

1. When a call comes in, the system extracts the DID number (the number being called)
2. It looks for a JSON file named `{DID}.json` in this directory
3. If found, it loads and merges the configuration with the base config
4. If not found, it uses `default.json` (if available) or falls back to base config
5. The merged configuration is used for that specific call

## Example: Multiple Restaurants

```
config/did/
├── 09154211914.json    # Bozorgmehr Restaurant
├── 02112345678.json    # Another Restaurant
├── 03134567890.json    # Third Restaurant
└── default.json        # Default fallback
```

Each restaurant can have:
- Different backend URLs
- Different AI personalities (via instructions)
- Different welcome messages
- Different voice settings
- Different menu items and context

## Environment Variable

You can set the config directory path using:
```bash
export DID_CONFIG_DIR=/path/to/config/did/
```

Default: `./config/did/`

## Notes

- Configuration files are cached after first load (for performance)
- Changes to JSON files require restarting the engine to take effect
- DID numbers are normalized (removes SIP URI prefixes, special chars)
- The system falls back gracefully if a config file is missing or invalid

