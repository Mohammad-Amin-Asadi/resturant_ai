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

import sys
import os
import logging
import argparse
from config import Config
from version import __version__


parser = argparse.ArgumentParser(description='OpenSIPS AI Voice Connector',
                                 prog=sys.argv[0],
                                 usage='%(prog)s [OPTIONS]',
                                 epilog='\n')
# Argument used to print the current version
parser.add_argument('-v', '--version',
                    action='version',
                    default=None,
                    version=f'OpenSIPS CLI {__version__}')
parser.add_argument('-c', '--config',
                    metavar='[CONFIG]',
                    type=str,
                    default=None,
                    help='specify a configuration file')

parsed_args = parser.parse_args()
Config.init(parsed_args.config)

# Configure logging level from environment variable
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, LOG_LEVEL, logging.INFO)
if not isinstance(log_level, int):
    log_level = logging.INFO

logging.basicConfig(
    level=log_level,
    format='%(asctime)s - tid: %(thread)d - %(levelname)s - %(message)s',
)
logging.info("Logging level set to: %s (from LOG_LEVEL=%s)", logging.getLevelName(log_level), LOG_LEVEL)

if __name__ == '__main__':
    try:
        from engine import run
        run()
    except ImportError as e:
        logging.error("Failed to import required modules: %s", e)
        logging.error("Please check that all dependencies are installed and PYTHONPATH is set correctly.")
        sys.exit(1)
    except Exception as e:
        logging.error("Fatal error during startup: %s", e, exc_info=True)
        sys.exit(1)

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
