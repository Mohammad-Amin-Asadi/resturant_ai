#!/usr/bin/env python
#
# Copyright (C) 2024 SIP Point Consulting SRL
#
# This file is part of the OpenSIPS AI Voice Connector project
# (see https://github.com/OpenSIPS/opensips-ai-voice-connector-ce).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

""" Main module that starts the Deepgram AI integration """

import json
import signal
import asyncio
import logging
import requests

from opensips.mi import OpenSIPSMI, OpenSIPSMIException
from opensips.event import OpenSIPSEventHandler, OpenSIPSEventException
from iranian_phone_validator import validate_caller_number, extract_config_number_from_from_header, clean_from_header_after_config_extraction
from phone_normalizer import normalize_phone_number
from aiortc.sdp import SessionDescription

from call import Call
from config import Config
from codec import UnsupportedCodec
from utils import UnknownSIPUser
import utils as utils

from engine_config import EngineConfig
from engine_utils.call_manager import CallManager

def is_ip_whitelisted(sdp_str, did_config=None):
    """
    بررسی اینکه آیا IP تماس‌گیرنده در whitelist هست
    
    Args:
        sdp_str: SDP string
        did_config: Optional DID config dictionary for tenant-specific whitelist
    """
    try:
        whitelist = EngineConfig.get_ip_whitelist(did_config)
        
        # استخراج IP از SDP
        for line in sdp_str.split('\n'):
            if line.startswith('o=') or line.startswith('c='):
                parts = line.split()
                if len(parts) >= 3:
                    ip = parts[-1].strip()
                    if ip in whitelist:
                        logging.info(f"✅ IP Whitelisted: {ip} - Accepting without filters")
                        return True, ip
        return False, None
    except Exception as e:
        logging.error(f"Error checking IP whitelist: {e}")
        return False, None


mi_cfg = Config.get("opensips")
mi_ip = mi_cfg.get("ip", "MI_IP", "127.0.0.1")
mi_port = int(mi_cfg.get("port", "MI_PORT", "8080"))

mi_conn = OpenSIPSMI(conn="datagram", datagram_ip=mi_ip, datagram_port=mi_port)

# Global call manager instance
call_manager = CallManager()


def mi_reply(key, method, code, reason, body=None):
    """ Replies to the server - handles errors gracefully """
    params = {'key': key,
              'method': method,
              'code': code,
              'reason': reason}
    if body:
        params["body"] = body
    
    try:
        mi_conn.execute('ua_session_reply', params)
    except OpenSIPSMIException as e:
        # If reply fails, log it but don't raise - transaction might already be closed
        error_msg = str(e)
        if "Failed to send reply" in error_msg or "transaction" in error_msg.lower():
            # This is expected when rejecting calls or transaction is already closed
            logging.debug(f"⚠️ Could not send reply {code} for {key}: {error_msg} (transaction may be closed)")
        else:
            logging.warning(f"⚠️ Failed to send reply {code} for {key}: {error_msg}")
        # Don't re-raise - we've already logged the issue


def fetch_bot_config(api_url, bot):
    """
    Sends a POST request to the API to fetch the bot configuration.

    :param api_url: URL of the API endpoint.
    :param bot: Name of the bot to fetch configuration for.
    :return: The configuration dictionary if successful, otherwise None.
    """
    try:
        response = requests.post(api_url, json={"bot": bot})
        if response.status_code == 200:
            return response.json()
        else:
            logging.exception(f"Failed to fetch data from API. Status: {response.status_code}, Message: {response.text}")
    except requests.RequestException as e:
        logging.exception(f"Error during API call: {e}")
    return None


def parse_params(params):
    """ Parses paraameters received in a call """
    # Log full params for debugging (can be verbose, so at DEBUG level)
    logging.debug("Received call params: %s", json.dumps(params, indent=2))
    
    # Extract and log Request-URI (DID) early
    request_uri = utils.get_request_uri(params)
    if request_uri:
        logging.info("📞 Request-URI (DID): %s", request_uri.uri if request_uri.uri else request_uri)
    
    flavor = None
    extra_params = None
    api_url = Config.engine("api_url", "API_URL")
    cfg = None
    bot = utils.get_user(params)
    to = utils.get_to(params)
    if bot and api_url:
        bot_data = fetch_bot_config(api_url, bot)
        if bot_data:
            flavor = bot_data.get('flavor')
            cfg = bot_data[flavor]

    if "extra_params" in params and params["extra_params"]:
        extra_params = json.loads(params["extra_params"])
        if "flavor" in extra_params:
            flavor = extra_params["flavor"]
    if not flavor:
        flavor = utils.get_ai_flavor(params)
    if extra_params and flavor in extra_params:
        if cfg is None:
            cfg = extra_params[flavor]
        else:
            cfg.update(extra_params[flavor])

    return flavor, to, cfg


def handle_call(call, key, method, params):
    """ Handles a SIP call """

    if method == 'INVITE':
        # ═══════════════════════════════════════════════════════════
        # لاگ کامل اطلاعات خام درخواست SIP
        # ═══════════════════════════════════════════════════════════
        logging.info("\n" + "=" * 100)
        logging.info("📞 COMPLETE SIP REQUEST DATA (RAW)")
        logging.info("=" * 100)
        logging.info("Call ID (B2B Key): %s", key)
        logging.info("Method: %s", method)
        logging.info("Full params dictionary:")
        for param_key, param_value in params.items():
            if param_key == 'body' and param_value:
                # برای body (SDP) فقط طول را نشان بده
                sdp_lines = param_value.split('\n') if isinstance(param_value, str) else []
                logging.info("  %s: [SDP Body - %d lines, %d chars]", param_key, len(sdp_lines), len(str(param_value)))
            else:
                logging.info("  %s: %s", param_key, param_value)
        
        # استخراج و نمایش تمام header های مهم
        logging.info("\n📋 SIP Headers:")
        headers_to_check = [
            "From", "To", "Call-ID", "CSeq", "Contact", "Via", 
            "User-Agent", "Allow", "Supported", "Content-Type",
            "Content-Length", "Route", "Record-Route", "Max-Forwards"
        ]
        for header_name in headers_to_check:
            header_value = utils.get_header(params, header_name)
            if header_value:
                logging.info("  %s: %s", header_name, header_value)
        
        # Request-URI
        request_uri = utils.get_request_uri(params)
        if request_uri:
            logging.info("  Request-URI: %s", request_uri)
        
        # SDP Body (complete)
        if 'body' in params and params['body']:
            logging.info("\n📄 SDP Body (Complete):")
            logging.info("-" * 100)
            sdp_str = params['body']
            for i, line in enumerate(sdp_str.split('\n'), 1):
                logging.info("  %3d: %s", i, line)
            logging.info("-" * 100)
        
        logging.info("=" * 100 + "\n")
        
        if 'body' not in params:
            mi_reply(key, method, 415, 'Unsupported Media Type')
            return

        sdp_str = params['body']
        
        # ═══════════════════════════════════════════════════════════
        # بررسی IP Whitelist (قبل از هر فیلتری)
        # ═══════════════════════════════════════════════════════════
        is_whitelisted, whitelisted_ip = is_ip_whitelisted(sdp_str)
        if is_whitelisted:
            logging.info(f"🎯 IP {whitelisted_ip} در whitelist است - تماس بدون محدودیت قبول می‌شود")
        
        # remove rtcp line, since the parser throws an error on it
        sdp_str = "\n".join([line for line in sdp_str.split("\n")
                             if not line.startswith("a=rtcp:")])
        sdp = SessionDescription.parse(sdp_str)

        if call:
            # handle in-dialog re-INVITE
            # Check if DID changed (IVR routing) - preserve original DID
            request_uri = utils.get_request_uri(params)
            new_did_number = None
            if request_uri and request_uri.uri:
                new_did_number = request_uri.uri.user if request_uri.uri.user else None
                if not new_did_number and request_uri.uri.host:
                    new_did_number = request_uri.uri.host.split('@')[0] if '@' in request_uri.uri.host else request_uri.uri.host
            
            # If DID changed and we don't have an original DID stored, store the current one as original
            if new_did_number and new_did_number != call.did_number:
                if not hasattr(call, 'original_did_number') or not call.original_did_number:
                    call.original_did_number = call.did_number
                call.did_number = new_did_number
                logging.info("🔄 DID changed from %s to %s (IVR routing detected)", 
                           call.original_did_number, new_did_number)
            
            direction = sdp.media[0].direction
            if not direction or direction == "sendrecv":
                call.resume()
            else:
                call.pause()
            try:
                mi_reply(key, method, 200, 'OK', call.get_body())
            except OpenSIPSMIException:
                logging.exception("Error sending response")
            return

        try:
            # Check if SDP has valid media section
            if not hasattr(sdp, 'media') or not sdp.media or len(sdp.media) == 0:
                logging.error("Invalid SDP format: no media section found")
                mi_reply(key, method, 488, 'Not Acceptable Here - Invalid SDP')
                return
            
            # ═══════════════════════════════════════════════════════════
            # استخراج From header (یک بار برای استفاده در همه جا)
            # ═══════════════════════════════════════════════════════════
            from_header = utils.get_header(params, "From")
            
            # ═══════════════════════════════════════════════════════════
            # اولویت 1: چک کردن الگوی "15923[شماره]-None" در From header
            # ═══════════════════════════════════════════════════════════
            config_number_from_from = extract_config_number_from_from_header(from_header)
            
            # ═══════════════════════════════════════════════════════════
            # Validation شماره موبایل (فقط برای non-whitelisted IPs)
            # ═══════════════════════════════════════════════════════════
            caller_number = None
            if not is_whitelisted:
                is_valid_phone, caller_number = validate_caller_number(from_header)

                if not is_valid_phone:
                    logging.warning("\n" + "=" * 80)
                    logging.warning("🚫 CALL REJECTED: Non-Iranian Mobile Number")
                    logging.warning("Call ID: %s", key)
                    logging.warning("From Header: %s", from_header)
                    logging.warning("Reason: Only Iranian mobile numbers are allowed")
                    logging.warning("=" * 80)
                    mi_reply(key, method, 403, 'Forbidden - Only Iranian Mobile Numbers Allowed')
                    return
                
                # Normalize the phone number
                caller_number = normalize_phone_number(caller_number)
                logging.info(f"✅ Valid Iranian mobile number: {caller_number}")
            else:
                # For whitelisted IPs, still try to extract phone number if available
                is_valid_phone, caller_number = validate_caller_number(from_header)
                if is_valid_phone:
                    caller_number = normalize_phone_number(caller_number)
                    logging.info(f"📞 Caller number: {caller_number}")
            
            flavor, to, cfg = parse_params(params)
            
            # Extract Request-URI (DID number - current number after IVR routing)
            request_uri = utils.get_request_uri(params)
            did_number = None
            if request_uri and request_uri.uri:
                did_number = request_uri.uri.user if request_uri.uri.user else None
                if not did_number and request_uri.uri.host:
                    # Sometimes the number is in the host part
                    did_number = request_uri.uri.host.split('@')[0] if '@' in request_uri.uri.host else request_uri.uri.host
            
            original_did_number = None
            if config_number_from_from:
                # اگر الگو را پیدا کردیم، از این شماره برای لود کانفیگ استفاده می‌کنیم
                original_did_number = config_number_from_from
                logging.info("🎯 Config number extracted from From header pattern: %s (current DID: %s)", 
                           config_number_from_from, did_number)
                
                # پاک کردن بخش "15923[شماره]-None" از From header
                from_header = clean_from_header_after_config_extraction(from_header)
                # استخراج مجدد caller_number از From header پاک شده
                is_valid_phone, caller_number = validate_caller_number(from_header)
                if is_valid_phone:
                    caller_number = normalize_phone_number(caller_number)
                    logging.info(f"📞 Caller number after cleaning From header: {caller_number}")
            else:
                # ═══════════════════════════════════════════════════════════
                # اگر الگو پیدا نشد، از DID برای لود کانفیگ استفاده می‌کنیم
                # ═══════════════════════════════════════════════════════════
                original_did_number = did_number
                logging.info("📞 Using DID number for config (no pattern in From header): %s", did_number)
            
            # Also extract from To header for comparison
            to_number = None
            if to and to.uri:
                to_number = to.uri.user if to.uri.user else None
            
            # Pass original_did_number to Call constructor so it's available when OpenAI is initialized
            new_call = Call(key, mi_conn, sdp, flavor, to, cfg, from_number=caller_number, did_number=did_number, original_did_number=original_did_number)
            logging.info("✅ Original DID set for config: %s (current DID: %s)", original_did_number, did_number)
            call_manager.add_call(key, new_call)
            
            logging.info("\n" + "=" * 80)
            logging.info("✅ CALL ACCEPTED")
            logging.info("Call ID: %s", key)
            logging.info("Caller (From): %s", caller_number or "unknown")
            logging.info("DID Number (Request-URI): %s", did_number or "unknown")
            logging.info("To Number: %s", to_number or "unknown")
            logging.info("Full To Header: %s", to)
            logging.info("Flavor: %s", flavor)
            logging.info("=" * 80)
            
            mi_reply(key, method, 200, 'OK', new_call.get_body())
        except UnsupportedCodec:
            logging.warning("\n" + "=" * 80)
            logging.warning("🚫 CALL REJECTED: Unsupported Codec")
            logging.warning("Call ID: %s", key)
            logging.warning("Reason: Codec not supported by system")
            logging.warning("=" * 80)
            mi_reply(key, method, 488, 'Not Acceptable Here')
        except UnknownSIPUser:
            logging.warning("\n" + "=" * 80)
            logging.warning("🚫 CALL REJECTED: Unknown SIP User")
            logging.warning("Call ID: %s", key)
            logging.warning("Reason: SIP user not found in configuration")
            logging.warning("=" * 80)
            mi_reply(key, method, 404, 'Not Found')
        except OpenSIPSMIException as e:
            # If we already tried to send a reply and it failed, don't try again
            error_msg = str(e)
            if "Failed to send reply" in error_msg:
                logging.debug("⚠️ Previous reply attempt failed, skipping additional reply")
            else:
                logging.warning(f"⚠️ OpenSIPS MI error: {error_msg}")
                # Only try to send error reply if it's not a "failed to send reply" error
                if "Failed to send reply" not in error_msg:
                    mi_reply(key, method, 500, 'Server Internal Error')
        except Exception as e:  # pylint: disable=broad-exception-caught
            logging.error("\n" + "=" * 80)
            logging.error("❌ CALL REJECTED: Unexpected Exception")
            logging.error("Call ID: %s", key)
            logging.error("Exception: %s", type(e).__name__)
            logging.error("Message: %s", str(e))
            logging.error("=" * 80)
            logging.exception("Full traceback:")
            # Try to send error reply, but don't fail if it doesn't work
            try:
                mi_reply(key, method, 500, 'Server Internal Error')
            except Exception:
                logging.debug("Could not send error reply (transaction may be closed)")
    
    elif method == 'NOTIFY':
        mi_reply(key, method, 200, 'OK')
        sub_state = utils.get_header(params, "Subscription-State")
        if "terminated" in sub_state:
            call.terminated = True
    
    elif method == 'BYE':
        logging.info("\n" + "=" * 80)
        logging.info("👋 CALL ENDED (BYE received)")
        logging.info("Call ID: %s", key)
        logging.info("=" * 80)
        asyncio.create_task(call.close())
        call_manager.remove_call(key)
    
    if not call:
        try:
            mi_reply(key, method, 405, 'Method not supported')
        except OpenSIPSMIException as e:
            logging.error(f"Failed to send reply {key}, {method}: {e}")
        return


def udp_handler(data):
    """ UDP handler of events received from OpenSIPS """
    
    logging.debug("📨 Received UDP event from OpenSIPS: %s", data)

    if 'params' not in data:
        logging.warning("⚠️  Received event without 'params' field: %s", data)
        return
    params = data['params']

    if 'key' not in params:
        logging.warning("⚠️  Received event without 'key' field: %s", params)
        return
    key = params['key']

    if 'method' not in params:
        logging.warning("⚠️  Received event without 'method' field: %s", params)
        return
    method = params['method']
    
    logging.info("🔔 OpenSIPS event received: method=%s, key=%s", method, key)
    
    if utils.indialog(params):
        # Sequential request within existing dialog
        logging.info("📞 Sequential request in dialog for call: %s", key)
        # search for the call
        if not call_manager.has_call(key):
            logging.warning("❌ Call not found for key %s (may have been terminated)", key)
            mi_reply(key, method, 481, 'Call/Transaction Does Not Exist')
            return
        call = call_manager.get_call(key)
        logging.info("✅ Found existing call: %s (DID: %s)", key, getattr(call, 'did_number', 'unknown'))
    else:
        # Initial request - new call
        logging.info("📞 Initial request - new call: %s", key)
        call = None

    handle_call(call, key, method, params)


async def shutdown(s, loop, event):
    """ Called when the program is shutting down """
    logging.info("Received exit signal %s...", s)
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    logging.info("Cancelling %d outstanding tasks", len(tasks))
    await call_manager.close_all()
    
    # Try to unsubscribe from events gracefully with timeout
    if event:
        try:
            # Close the socket first to release the port
            if hasattr(event, 'socket') and hasattr(event.socket, 'sock'):
                try:
                    event.socket.sock.close()
                    logging.info("Closed event socket")
                except Exception as e:
                    logging.debug("Error closing socket: %s", e)
            
            # Use asyncio.wait_for to add a timeout to unsubscribe
            await asyncio.wait_for(
                asyncio.to_thread(event.unsubscribe),
                timeout=2.0
            )
            logging.info("Successfully unsubscribed from OpenSIPS events")
        except asyncio.TimeoutError:
            logging.warning("Timeout unsubscribing from OpenSIPS events (OpenSIPS may be unavailable)")
        except OpenSIPSEventException as e:
            error_msg = str(e)
            if "timed out" in error_msg.lower() or "connection" in error_msg.lower():
                logging.warning("Could not unsubscribe from events (OpenSIPS connection issue): %s", e)
            else:
                logging.warning("Error unsubscribing from event: %s", e)
        except OpenSIPSMIException as e:
            error_msg = str(e)
            if "timed out" in error_msg.lower() or "connection" in error_msg.lower():
                logging.warning("Could not unsubscribe from events (OpenSIPS connection issue): %s", e)
            else:
                logging.warning("Error unsubscribing from event: %s", e)
        except Exception as e:
            # Catch any other exceptions during unsubscribe
            logging.warning("Unexpected error during unsubscribe (non-critical): %s", e)
    
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()
    logging.info("Shutdown complete.")


async def async_run():
    """ Main function """
    # Default to 0.0.0.0 for Docker compatibility (allows connections from outside container)
    # If OpenSIPS is on the same host, it can connect via exposed port
    host_ip = Config.engine("event_ip", "EVENT_IP", "0.0.0.0")
    # Use port 0 (random port) to avoid conflicts with stale processes
    # This allows the OS to assign any available port
    port = int(Config.engine("event_port", "EVENT_PORT", "0"))
    
    logging.info("🔧 Engine network configuration:")
    logging.info("   Event listener IP: %s", host_ip)
    logging.info("   Event listener port: %s (auto-assigned)", port if port == 0 else port)
    logging.info("   OpenSIPS MI connection: %s:%d", mi_ip, mi_port)
    
    # Wait for OpenSIPS to be ready and any stale sockets to be released
    await asyncio.sleep(1.0)
    
    handler = OpenSIPSEventHandler(mi_conn, "datagram", ip=host_ip, port=port)
    
    # Retry logic for "Address already in use" error
    # If port is 0, this should rarely happen, but we handle it anyway
    max_retries = 10
    retry_delay = 0.5  # Start with 0.5 seconds
    event = None
    
    for attempt in range(max_retries):
        try:
            event = handler.async_subscribe("E_UA_SESSION", udp_handler)
            break  # Success, exit retry loop
        except (OpenSIPSEventException, OSError, Exception) as e:
            # Check if it's an "Address already in use" error
            is_address_in_use = False
            
            # Check OSError directly
            if isinstance(e, OSError) and e.errno == 98:  # EADDRINUSE
                is_address_in_use = True
            # Check exception message
            elif "address already in use" in str(e).lower() or "errno 98" in str(e).lower():
                is_address_in_use = True
            # Check exception chain (in case OSError is wrapped)
            elif hasattr(e, '__cause__') and isinstance(e.__cause__, OSError):
                if e.__cause__.errno == 98:
                    is_address_in_use = True
            elif hasattr(e, '__context__') and isinstance(e.__context__, OSError):
                if e.__context__.errno == 98:
                    is_address_in_use = True
            
            if is_address_in_use:
                if attempt < max_retries - 1:
                    logging.warning(
                        "Address already in use (attempt %d/%d). "
                        "Waiting %.1f seconds before retry...",
                        attempt + 1, max_retries, retry_delay
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    logging.error(
                        "Failed to bind socket after %d attempts. "
                        "The address may still be in use. Error: %s",
                        max_retries, e
                    )
                    return
            else:
                # Different error, don't retry
                logging.error("Error subscribing to event: %s", e)
                return
    
    if event is None:
        logging.error("Failed to subscribe to event after all retries")
        return

    _, actual_port = event.socket.sock.getsockname()
    actual_ip, _ = event.socket.sock.getsockname()

    logging.info("\n" + "╔" + "=" * 78 + "╗")
    logging.info("║" + " " * 15 + "🍽️  RESTAURANT ORDERING SYSTEM (Bozorgmehr) 🍽️" + " " * 14 + "║")
    logging.info("║" + " " * 78 + "║")
    logging.info("║  System: OpenAI Realtime + Soniox STT (Persian)" + " " * 28 + "║")
    logging.info("║  Features: Order Taking, Tracking, Menu Recommendations" + " " * 19 + "║")
    logging.info("╚" + "=" * 78 + "╝")
    logging.info("\n🚀 Engine started successfully!")
    logging.info("   Listening for OpenSIPS events on: %s:%d", actual_ip, actual_port)
    logging.info("   OpenSIPS MI: %s:%d", mi_ip, mi_port)
    logging.info("   ⚠️  IMPORTANT: Configure OpenSIPS event_datagram to send events to this address")
    logging.info("   Waiting for incoming calls...")

    loop = asyncio.get_running_loop()
    stop = loop.create_future()

    loop.add_signal_handler(
        signal.SIGTERM,
        lambda: asyncio.create_task(shutdown(signal.SIGTERM,
                                             loop,
                                             event)),
    )

    loop.add_signal_handler(
        signal.SIGINT,
        lambda: asyncio.create_task(shutdown(signal.SIGINT,
                                             loop,
                                             event)),
    )

    try:
        await stop
    except asyncio.CancelledError:
        pass


def run():
    """ Runs the entire engine asynchronously """
    asyncio.run(async_run())

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
