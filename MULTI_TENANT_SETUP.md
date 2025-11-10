# Multi-Tenant DID-Based Configuration Guide

This guide explains how to set up multiple restaurant agents on a single server using DID-based configuration.

## Overview

The system now supports **multi-tenant operation** where different DID numbers (phone numbers being called) can have completely different configurations. This allows one server to serve multiple restaurants, each with their own:

- AI personality and instructions
- Backend API endpoints
- Welcome messages
- Voice settings
- Menu items and context

## Architecture

```
Incoming Call → Extract DID Number → Load {DID}.json → Merge with Base Config → Use for Call
```

## Setup Steps

### 1. Create Configuration Directory

```bash
mkdir -p /home/opensip_stabel/engine/config/did
```

Or set a custom path:
```bash
export DID_CONFIG_DIR=/path/to/your/config/did/
```

### 2. Create DID Configuration Files

For each restaurant/DID number, create a JSON file named after the DID:

**Example: `/home/opensip_stabel/engine/config/did/09154211914.json`**

```json
{
  "restaurant_name": "رستوران بزرگمهر",
  "restaurant_id": "bozorgmehr",
  "description": "Bozorgmehr Restaurant Configuration",
  
  "backend_url": "http://localhost:8000",
  
  "openai": {
    "model": "gpt-realtime-2025-08-28",
    "voice": "alloy",
    "temperature": 0.8,
    "welcome_message": "سلام و درودبرشما، با رستوران بزرگمهر تماس گرفته‌اید.",
    "intro": "سلام و درودبرشما، با رستوران بزرگمهر تماس گرفته‌اید."
  },
  
  "soniox": {
    "enabled": true,
    "model": "stt-rt-preview",
    "language_hints": ["fa"]
  }
}
```

**Example: `/home/opensip_stabel/engine/config/did/02112345678.json`**

```json
{
  "restaurant_name": "رستوران دیگری",
  "restaurant_id": "another_restaurant",
  "description": "Another Restaurant Configuration",
  
  "backend_url": "http://another-backend:8000",
  
  "openai": {
    "model": "gpt-realtime-2025-08-28",
    "voice": "nova",
    "temperature": 0.9,
    "welcome_message": "سلام، به رستوران دیگری خوش آمدید.",
    "intro": "سلام، به رستوران دیگری خوش آمدید."
  },
  
  "soniox": {
    "enabled": true,
    "model": "stt-rt-preview",
    "language_hints": ["fa"]
  }
}
```

### 3. Create Default Configuration (Optional)

Create `default.json` for fallback when no DID-specific config exists:

```bash
cp /home/opensip_stabel/engine/config/did/09154211914.json \
   /home/opensip_stabel/engine/config/did/default.json
```

### 4. Restart the Engine

After creating configuration files, restart the engine:

```bash
docker restart engine
```

Or if running directly:
```bash
# Stop the engine
# Start it again
```

## How It Works

### 1. Call Routing

When a caller dials a DID number:
- System extracts the DID from the Request-URI
- Looks for `{DID}.json` in the config directory
- Loads and merges with base configuration
- Uses merged config for that call

### 2. Configuration Merging

DID-specific values **override** base config values:
- Base config: `config.ini` (or environment variables)
- DID config: `config/did/{DID}.json`
- Final config: DID config values take precedence

### 3. Per-Call Isolation

Each call gets its own:
- AI agent instance with DID-specific instructions
- Backend API client (can point to different servers)
- Welcome message and personality
- All settings from the JSON file

## Configuration Options

### Backend URL

Each restaurant can have its own backend:

```json
{
  "backend_url": "http://restaurant1-backend:8000"
}
```

This allows:
- Different databases per restaurant
- Different menu items
- Different order management systems
- Complete isolation between tenants

### OpenAI Settings

Customize AI behavior per restaurant:

```json
{
  "openai": {
    "voice": "nova",           // Different voice per restaurant
    "temperature": 0.9,        // Different creativity level
    "welcome_message": "...",  // Custom greeting
    "intro": "..."            // Custom introduction
  }
}
```

### Soniox STT Settings

Customize speech recognition per restaurant:

```json
{
  "soniox": {
    "model": "stt-rt-preview",
    "language_hints": ["fa", "en"],  // Multi-language support
    "upsample_audio": true,
    "silence_duration_ms": 500
  }
}
```

### Custom Context

Add restaurant-specific data:

```json
{
  "custom_context": {
    "menu_items": ["کباب", "جوجه", "پیتزا"],
    "special_offers": "پیشنهاد ویژه امروز...",
    "restaurant_info": {
      "address": "تهران، خیابان...",
      "phone": "021-12345678"
    }
  }
}
```

## Example Use Cases

### Use Case 1: Multiple Restaurants, Same Backend

All restaurants use the same backend but different personalities:

```json
// 09154211914.json
{
  "backend_url": "http://shared-backend:8000",
  "openai": {
    "welcome_message": "سلام، رستوران بزرگمهر..."
  }
}

// 02112345678.json
{
  "backend_url": "http://shared-backend:8000",
  "openai": {
    "welcome_message": "سلام، رستوران دیگری..."
  }
}
```

### Use Case 2: Multiple Restaurants, Different Backends

Each restaurant has its own backend:

```json
// 09154211914.json
{
  "backend_url": "http://bozorgmehr-backend:8000"
}

// 02112345678.json
{
  "backend_url": "http://another-backend:8000"
}
```

### Use Case 3: Different Languages

Some restaurants support multiple languages:

```json
{
  "soniox": {
    "language_hints": ["fa", "en", "ar"]
  },
  "openai": {
    "welcome_message": "Welcome! You can speak Persian, English, or Arabic."
  }
}
```

## Logging

The system logs which configuration is being used:

```
🔧 Loading DID-specific config for: 09154211914
✅ DID config loaded: ['restaurant_name', 'backend_url', 'openai', 'soniox']
🔗 Using DID-specific backend URL: http://bozorgmehr-backend:8000
```

## Troubleshooting

### Config Not Loading

1. Check file exists: `ls /home/opensip_stabel/engine/config/did/{DID}.json`
2. Check JSON syntax: `python -m json.tool {DID}.json`
3. Check logs for errors
4. Verify DID number extraction in logs

### Using Default Config

If you see:
```
⚠️  No DID config found for 09154211914, using defaults
```

- Create the JSON file for that DID
- Or create `default.json` for fallback

### Backend URL Not Working

- Verify the backend URL is accessible from the engine
- Check network connectivity
- Verify the backend API is running

## Best Practices

1. **Always create a `default.json`** for fallback
2. **Use descriptive restaurant_id** for logging
3. **Test each DID configuration** before going live
4. **Keep JSON files in version control**
5. **Document custom configurations** in comments
6. **Use environment variables** for sensitive data (API keys)

## File Structure

```
opensip_stabel/engine/
├── src/
│   ├── did_config.py          # DID config loader
│   ├── openai_api.py          # Modified to use DID config
│   ├── engine.py              # Modified to extract DID
│   └── call.py                # Modified to store DID
└── config/
    ├── config.ini             # Base configuration
    └── did/                    # DID-specific configs
        ├── 09154211914.json
        ├── 02112345678.json
        └── default.json
```

## Next Steps

1. Create configuration files for each restaurant
2. Test with actual calls
3. Monitor logs to verify correct config loading
4. Adjust configurations based on feedback
5. Scale to more restaurants as needed

## Support

For issues or questions:
- Check logs: `docker logs engine`
- Verify DID extraction: Look for "DID Number (Request-URI)" in logs
- Test config loading: Check for "✅ DID config loaded" messages

