# DID Number Explanation and Logging Guide

## What is a DID Number?

**DID (Direct Inward Dialing)** is the phone number that a caller dials to reach your server. It's also known as the **Request-URI** in SIP terminology.

### Key Concepts:

1. **Request-URI (DID)**: The actual number the caller dialed (e.g., `sip:1234567890@yourdomain.com`)
2. **To Header**: The intended recipient (usually the same as Request-URI for initial INVITE)
3. **From Header**: The caller's phone number

For initial INVITE requests, the Request-URI and To header are typically the same. However, they can differ in some scenarios (e.g., call forwarding, redirects).

## How to View DID Numbers in Logs

### 1. OpenSIPS Logs

The OpenSIPS configuration now logs the Request-URI (DID) directly:

```bash
# View OpenSIPS logs
docker logs opensips

# Or if running directly
tail -f /var/log/opensips.log
```

You'll see output like:
```
========== NEW REQUEST ==========
Method: INVITE
Request-URI (DID): sip:1234567890@yourdomain.com
From: sip:09123456789@callerdomain.com
To: sip:1234567890@yourdomain.com
Source: 192.168.1.100:5060
================================
```

### 2. Python Engine Logs

The Python engine now extracts and logs the DID number prominently:

```bash
# View engine logs
docker logs engine

# Or if running directly
tail -f /path/to/engine.log
```

You'll see output like:
```
================================================================================
✅ CALL ACCEPTED
Call ID: abc123
Caller (From): 09123456789
DID Number (Request-URI): 1234567890
To Number: 1234567890
Full To Header: <sip:1234567890@yourdomain.com>
Flavor: openai
================================================================================
```

### 3. Real-time Monitoring

To monitor calls in real-time:

```bash
# Watch OpenSIPS logs
docker logs -f opensips

# Watch engine logs
docker logs -f engine

# Or use your monitor script
./monitor.sh
```

## Code Changes Made

### 1. OpenSIPS Configuration (`opensips.cfg`)
- Added logging for Request-URI using `$ru` variable
- Now logs: `Request-URI (DID): $ru`

### 2. Python Utils (`utils.py`)
- Added `get_request_uri()` function to extract Request-URI from SIP parameters
- Falls back to To header if Request-URI not directly available

### 3. Python Engine (`engine.py`)
- Enhanced logging to show:
  - **DID Number (Request-URI)**: The actual number dialed
  - **To Number**: For comparison
  - **Caller (From)**: The caller's number
- Changed verbose params logging to DEBUG level

## Understanding the Output

When you see logs like:
```
DID Number (Request-URI): 1234567890
To Number: 1234567890
```

- If they match: Normal call, caller dialed the number directly
- If they differ: May indicate call forwarding, redirect, or special routing

## Troubleshooting

If you see "unknown" for DID Number:
1. Check OpenSIPS logs - the Request-URI should be logged there
2. The Request-URI might be the same as the To header (which is logged)
3. Enable DEBUG logging to see full SIP parameters:
   ```python
   logging.basicConfig(level=logging.DEBUG)
   ```

## Example Log Output

**OpenSIPS Log:**
```
[INFO] ========== NEW REQUEST ==========
[INFO] Method: INVITE
[INFO] Request-URI (DID): sip:09154211914@yourdomain.com
[INFO] From: sip:09123456789@callerdomain.com
[INFO] To: sip:09154211914@yourdomain.com
```

**Python Engine Log:**
```
[INFO] 📞 Request-URI (DID): sip:09154211914@yourdomain.com
[INFO] ================================================================================
[INFO] ✅ CALL ACCEPTED
[INFO] Call ID: xyz789
[INFO] Caller (From): 09123456789
[INFO] DID Number (Request-URI): 09154211914
[INFO] To Number: 09154211914
[INFO] Full To Header: <sip:09154211914@yourdomain.com>
[INFO] Flavor: openai
[INFO] ================================================================================
```

This tells you:
- **Caller dialed**: 09154211914 (the DID number)
- **Caller's number**: 09123456789
- **Both match**: Normal direct call

