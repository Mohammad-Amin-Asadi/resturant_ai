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
