# Scenario Configuration Guide

This guide explains how to configure different scenarios, instructions, welcome messages, and function calls via DID configuration files.

## Overview

The system now supports **fully configurable scenarios** through JSON files. You can customize:

- **Base Instructions**: Core AI personality and behavior
- **Welcome Messages**: Greetings for different scenarios
- **Scenario Instructions**: Specific instructions for different call situations
- **Function Definitions**: Customize function call descriptions and parameters
- **Status Messages**: Messages for different order statuses

## Configuration Structure

### Base Configuration

```json
{
  "restaurant_name": "نام رستوران",
  "instructions_base": "دستورالعمل‌های پایه...",
  "scenarios": { ... },
  "functions": { ... }
}
```

### Scenarios Section

The `scenarios` section contains configuration for different call situations:

```json
{
  "scenarios": {
    "has_orders": {
      "welcome_templates": { ... },
      "single_order_template": "...",
      "multiple_orders_template": "...",
      "status_messages": { ... }
    },
    "new_customer": {
      "welcome_templates": { ... },
      "new_order_template": "..."
    }
  }
}
```

## Welcome Message Templates

### For Customers with Orders

```json
{
  "has_orders": {
    "welcome_templates": {
      "with_customer_name": "سلام {customer_name} عزیز، با {restaurant_name} تماس گرفته‌اید",
      "without_customer_name": "سلام، با {restaurant_name} تماس گرفته‌اید",
      "closing_with_orders": " از صبر شما متشکریم."
    }
  }
}
```

**Template Variables:**
- `{customer_name}` - Customer's name from history
- `{restaurant_name}` - Restaurant name from config

### For New Customers

```json
{
  "new_customer": {
    "welcome_templates": {
      "with_customer_name": "سلام {customer_name} عزیز...",
      "without_customer_name": "سلام...",
      "new_customer_question": " آیا می‌خواهید سفارش جدیدی ثبت کنید؟"
    }
  }
}
```

## Scenario Instructions

### Single Order Template

When customer has exactly one undelivered order:

```json
{
  "single_order_template": "مشتری سفارش #{order_id} ({status_display}) دارد. 1) وضعیت را تایید کنید..."
}
```

**Template Variables:**
- `{order_id}` - Order ID
- `{status_display}` - Order status display name

### Multiple Orders Template

When customer has multiple undelivered orders:

```json
{
  "multiple_orders_template": "مشتری {orders_count} سفارش تحویل نشده دارد: {order_ids}..."
}
```

**Template Variables:**
- `{orders_count}` - Number of orders
- `{order_ids}` - Comma-separated list of order IDs

### New Order Template

For new customers or when all orders are delivered:

```json
{
  "new_order_template": "وظیفه: دریافت سفارش جدید. {name_instruction} مراحل: 1) پیشنهادات ویژه..."
}
```

**Template Variables:**
- `{name_instruction}` - Instruction about asking for name (auto-filled)

## Status Messages

Messages shown based on order status:

```json
{
  "status_messages": {
    "pending": "نکته: سفارش در حال تایید است...",
    "preparing": "نکته: سفارش در حال آماده سازی است...",
    "on_delivery": "نکته: سفارش در راه است..."
  }
}
```

## Function Definitions

You can customize function call definitions:

```json
{
  "functions": {
    "track_order": {
      "type": "function",
      "name": "track_order",
      "description": "پیگیری سفارش - متن سفارشی",
      "parameters": {
        "type": "object",
        "properties": {
          "phone_number": {
            "type": "string",
            "description": "شماره تلفن (اختیاری)"
          }
        },
        "required": []
      }
    },
    "create_order": {
      "type": "function",
      "name": "create_order",
      "description": "ثبت سفارش - متن سفارشی",
      "parameters": { ... }
    }
  }
}
```

**Note:** If you provide `functions` as a dictionary, it will merge with default functions. If you provide it as a list, it will replace all default functions.

## Complete Example

```json
{
  "restaurant_name": "رستوران بزرگمهر",
  "backend_url": "http://localhost:8000",
  
  "instructions_base": "شما دستیار هوشمند رستوران بزرگمهر هستید. فقط فارسی صحبت کنید. لحن: گرم، پرانرژی، مودب، حرفه‌ای. {name_instruction} شماره تلفن خودکار است.",
  
  "scenarios": {
    "has_orders": {
      "welcome_templates": {
        "with_customer_name": "سلام {customer_name} عزیز، با {restaurant_name} تماس گرفته‌اید",
        "without_customer_name": "سلام، با {restaurant_name} تماس گرفته‌اید",
        "closing_with_orders": " از صبر شما متشکریم."
      },
      "single_order_template": "مشتری سفارش #{order_id} ({status_display}) دارد. 1) وضعیت را تایید کنید.",
      "multiple_orders_template": "مشتری {orders_count} سفارش دارد: {order_ids}.",
      "status_messages": {
        "pending": "سفارش در حال تایید است.",
        "preparing": "سفارش در حال آماده سازی است.",
        "on_delivery": "سفارش در راه است."
      }
    },
    "new_customer": {
      "welcome_templates": {
        "with_customer_name": "سلام {customer_name} عزیز...",
        "without_customer_name": "سلام...",
        "new_customer_question": " آیا می‌خواهید سفارش جدیدی ثبت کنید؟"
      },
      "new_order_template": "وظیفه: دریافت سفارش جدید. {name_instruction} مراحل: 1) پیشنهادات ویژه..."
    }
  }
}
```

## How It Works

1. **Call comes in** → System extracts DID number
2. **Loads DID config** → Reads `{DID}.json` file
3. **Determines scenario** → Checks if customer has orders
4. **Builds instructions** → Uses templates from config
5. **Formats messages** → Replaces template variables
6. **Applies to call** → Uses customized configuration

## Template Variable Reference

### Welcome Messages
- `{customer_name}` - Customer name from history
- `{restaurant_name}` - Restaurant name from config

### Order Scenarios
- `{order_id}` - Single order ID
- `{status_display}` - Order status display name
- `{orders_count}` - Number of orders
- `{order_ids}` - Comma-separated order IDs

### Instructions
- `{name_instruction}` - Auto-filled instruction about asking for name

## Best Practices

1. **Keep templates concise** - Long templates may confuse the AI
2. **Use clear Persian** - Write naturally in Persian
3. **Test scenarios** - Test both "has orders" and "new customer" scenarios
4. **Customize per restaurant** - Each restaurant can have different personality
5. **Version control** - Keep config files in version control

## Fallback Behavior

If a template is missing from DID config:
- System uses hardcoded defaults
- Logs a warning message
- Continues with default behavior

## Troubleshooting

### Template Not Working

1. Check JSON syntax: `python -m json.tool config.json`
2. Verify template variables match exactly (case-sensitive)
3. Check logs for template loading messages

### Variables Not Replaced

- Ensure variable names match exactly: `{order_id}` not `{orderId}`
- Check that data is available (e.g., order_id exists)

### Custom Functions Not Loading

- Verify `functions` is a list or dict
- Check function structure matches OpenAI format
- Review logs for function loading messages

## Advanced: Multiple Scenarios

You can define additional scenarios beyond the default two:

```json
{
  "scenarios": {
    "has_orders": { ... },
    "new_customer": { ... },
    "vip_customer": {
      "welcome_templates": { ... },
      "instructions": "..."
    }
  }
}
```

Then modify the code to detect VIP customers and use the `vip_customer` scenario.

