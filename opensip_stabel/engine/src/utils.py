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

"""
Module that provides helper functions for AI
"""

import re
import logging
from sipmessage import Address
from deepgram_api import Deepgram
from openai_api import OpenAI
from deepgram_native_api import DeepgramNative
#from azure_api import AzureAI
from config import Config

FLAVORS = {"deepgram": Deepgram,
           "openai": OpenAI,
           "deepgram_native": DeepgramNative,
           #"azure": AzureAI
           }


class UnknownSIPUser(Exception):
    """ User is not known """


def get_header(params, header):
    """ Returns a specific line from headers """
    if 'headers' not in params:
        return None
    hdr_lines = [line for line in params['headers'].splitlines()
                 if re.match(f"{header}:", line, re.I)]
    if len(hdr_lines) == 0:
        return None
    return hdr_lines[0].split(":", 1)[1].strip()


def get_to(params):
    """ Returns the To line parameters """
    to_line = get_header(params, "To")
    if not to_line:
        return None
    return Address.parse(to_line)


def indialog(params):
    """ indicates whether the message is an in-dialog one """
    if 'headers' not in params:
        return False
    to = get_to(params)
    if not to:
        return False
    params = to.parameters
    if "tag" in params and len(params["tag"]) > 0:
        return True
    return False


def get_user(params):
    """ Returns the User from the SIP headers """

    to = get_to(params)
    return to.uri.user.lower() if to.uri else None


def get_original_did_from_headers(params):
    """
    Extract original called number (DID) from SIP headers.
    Checks History-Info, Diversion, and P-Asserted-Identity headers.
    This is useful when IVR routes the call and changes the Request-URI.
    
    Args:
        params: SIP parameters dictionary
        
    Returns:
        Original DID number as string, or None if not found
    """
    if 'headers' not in params:
        logging.debug("No headers in params for original DID extraction")
        return None
    
    headers = params['headers']
    
    # Log available headers for debugging (only if we don't find original DID)
    available_headers = []
    for line in headers.splitlines():
        if ':' in line:
            header_name = line.split(':', 1)[0].strip()
            if header_name.lower() in ['history-info', 'diversion', 'p-asserted-identity', 'p-called-party-id']:
                available_headers.append(header_name)
    
    # Try History-Info header (most common for call forwarding/IVR)
    # Format: History-Info: <sip:511882@domain>;index=1, <sip:1@domain>;index=2
    history_info = get_header(params, "History-Info")
    if history_info:
        logging.debug("Found History-Info header: %s", history_info)
        # Get the first entry (original called number)
        # History-Info entries are comma-separated, first one is usually the original
        entries = history_info.split(',')
        if entries:
            first_entry = entries[0].strip()
            # Extract number from <sip:number@domain>
            match = re.search(r'<sip:([^@>]+)@', first_entry)
            if match:
                original_did = match.group(1)
                logging.info("📞 Original DID from History-Info: %s", original_did)
                return original_did
    
    # Try Diversion header
    # Format: Diversion: <sip:511882@domain>;reason=unconditional
    diversion = get_header(params, "Diversion")
    if diversion:
        logging.debug("Found Diversion header: %s", diversion)
        match = re.search(r'<sip:([^@>]+)@', diversion)
        if match:
            original_did = match.group(1)
            logging.info("📞 Original DID from Diversion: %s", original_did)
            return original_did
    
    # Try P-Called-Party-ID header (sometimes used by PBX systems)
    p_called = get_header(params, "P-Called-Party-ID")
    if p_called:
        logging.debug("Found P-Called-Party-ID header: %s", p_called)
        match = re.search(r'<sip:([^@>]+)@', p_called)
        if match:
            original_did = match.group(1)
            logging.info("📞 Original DID from P-Called-Party-ID: %s", original_did)
            return original_did
    
    # Try P-Asserted-Identity (less common for DID, but worth checking)
    p_asserted = get_header(params, "P-Asserted-Identity")
    if p_asserted:
        logging.debug("Found P-Asserted-Identity header: %s", p_asserted)
        match = re.search(r'<sip:([^@>]+)@', p_asserted)
        if match:
            original_did = match.group(1)
            logging.info("📞 Original DID from P-Asserted-Identity: %s", original_did)
            return original_did
    
    if available_headers:
        logging.debug("Checked headers for original DID: %s (none contained original DID)", ", ".join(available_headers))
    else:
        logging.debug("No relevant headers found for original DID extraction")
    
    return None


def get_request_uri(params):
    """
    Extracts the Request-URI (DID number) from SIP parameters.
    The Request-URI is the actual number the caller dialed.
    
    Args:
        params: SIP parameters dictionary
        
    Returns:
        Address object with Request-URI, or None if not found
    """
    # Try to get Request-URI from headers (it might be in the first line or as a header)
    if 'headers' not in params:
        return None
    
    # The Request-URI might be passed as a parameter or we can extract from To header
    # For initial INVITE, Request-URI is usually the same as To header
    # But let's try to get it from the headers if available
    headers = params['headers']
    
    # Check if there's a Request-URI line in headers
    # Sometimes OpenSIPS passes it, sometimes we use To header as fallback
    for line in headers.splitlines():
        # Look for Request-URI pattern: INVITE sip:number@domain SIP/2.0
        if line.strip().startswith('INVITE') or line.strip().startswith('sip:'):
            # Try to parse as Request-URI
            match = re.search(r'sip:([^@\s]+)@?([^\s]*)', line)
            if match:
                try:
                    # Construct a To-like header to parse
                    uri_str = f"sip:{match.group(1)}@{match.group(2) if match.group(2) else 'unknown'}"
                    return Address.parse(uri_str)
                except:
                    pass
    
    # Fallback: Use To header (for initial INVITE, they're usually the same)
    return get_to(params)


def _dialplan_match(regex, string):
    """ Checks if a regex matches the string """
    pattern = re.compile(regex)
    return pattern.match(string)


def get_ai_flavor_default(user):
    """ Returns the default algorithm for AI choosing """
    # remove disabled engines
   # keys = [k for k, _ in FLAVORS.items() if
       #     not Config.get(k).getboolean("disabled",
      #                                   f"{k.upper()}_DISABLE",
     #                                    False)]
    #if user in keys:
    #    return user
   # hash_index = hash(user) % len(keys)
  # return keys[hash_index]
    return "openai"

def get_ai_flavor(params):
    """ Returns the AI flavor to be used """

    user = get_user(params)
    if not user:
        raise UnknownSIPUser("cannot parse username")

    # first, get the sections in order and check if they have a dialplan
    flavor = None
    for flavor in Config.sections():
        if flavor not in FLAVORS:
            continue
        if Config.get(flavor).getboolean("disabled",
                                         f"{flavor.upper()}_DISABLE",
                                         False):
            continue
        dialplans = Config.get(flavor).get("match")
        if not dialplans:
            continue
        if isinstance(dialplans, list):
            for dialplan in dialplans:
                if _dialplan_match(dialplan, user):
                    return flavor
        elif _dialplan_match(dialplans, user):
            return flavor
    #return get_ai_flavor_default(user)
    return "openai" 

def get_ai(flavor, call, cfg):
    """ Returns an AI object """
    return FLAVORS[flavor](call, cfg)

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
