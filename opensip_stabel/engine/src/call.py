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

""" Handles the a SIP call """

import random
import socket
import asyncio
import logging
import secrets
import datetime
from queue import Queue, Empty
from aiortc.sdp import SessionDescription
from config import Config

from rtp import decode_rtp_packet, generate_rtp_packet
from utils import get_ai
from engine_utils.port_manager import PortManager

rtp_cfg = Config.get("rtp")
min_rtp_port = int(rtp_cfg.get("min_port", "RTP_MIN_PORT", "35000"))
max_rtp_port = int(rtp_cfg.get("max_port", "RTP_MAX_PORT", "65000"))

# Global port manager instance
_port_manager = PortManager(min_rtp_port, max_rtp_port)


class NoAvailablePorts(Exception):
    """ There are no available ports """
    
    def __init__(self, message="No available ports"):
        self.message = message
        super().__init__(self.message)


class Call():  # pylint: disable=too-many-instance-attributes
    """ Class that handles a call """
    # pylint: disable=too-many-arguments, too-many-positional-arguments
    def __init__(self,
                 b2b_key,
                 mi_conn,
                 sdp: SessionDescription,
                 flavor: str,
                 to: str,
                 cfg,
                 from_number=None,
                 did_number=None,
                 original_did_number=None):
        host_ip = rtp_cfg.get('bind_ip', 'RTP_BIND_IP', '0.0.0.0')
        try:
            hostname = socket.gethostbyname(socket.gethostname())
        except socket.gaierror:  # unknown hostname
            hostname = "127.0.0.1"
        rtp_ip = rtp_cfg.get('ip', 'RTP_IP', hostname)
        logging.info("RTP settings - bind_ip: %s, rtp_ip: %s", host_ip, rtp_ip)
    
        self.b2b_key = b2b_key
        self.mi_conn = mi_conn
        self.from_number = from_number  # Store caller's phone number
        self.did_number = did_number  # Store DID number (the number being called)
        # Use provided original_did_number, or fallback to did_number
        self.original_did_number = original_did_number if original_did_number is not None else did_number

        if hasattr(sdp, 'media') and sdp.media and len(sdp.media) > 0:
            if hasattr(sdp.media[0], 'host') and sdp.media[0].host:
                self.client_addr = sdp.media[0].host
            else:
                self.client_addr = sdp.host if hasattr(sdp, 'host') and sdp.host else "127.0.0.1"
        else:
            self.client_addr = sdp.host if hasattr(sdp, 'host') and sdp.host else "127.0.0.1"
        self.client_port = sdp.media[0].port
        self.paused = False
        self.terminated = False

        self.rtp = Queue()
        self.stop_event = asyncio.Event()
        self.stop_event.clear()

        self.to = to
        self.sdp = sdp
        self.ai = get_ai(flavor, self, cfg)

        self.codec = self.ai.get_codec()

        self.serversock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.bind(host_ip)
        self.serversock.setblocking(False)
        
        # Log RTP socket details
        sock_addr = self.serversock.getsockname()
        logging.info("🎧 RTP socket bound: %s:%d (fileno: %d)", sock_addr[0], sock_addr[1], self.serversock.fileno())

        self.sdp = self.get_new_sdp(sdp, rtp_ip)
        
        # Log SDP details for debugging
        logging.info("📋 SDP details: IP=%s, Port=%d, Codec=%s", 
                   self.sdp.media[0].host, self.sdp.media[0].port, self.codec.name)
        
        # Log remote RTP address from SDP
        logging.info("🎯 RTP remote address set to %s:%d from INVITE SDP", self.client_addr, self.client_port)
        
        # پخش چند فریم سکوت در ابتدا (100 frames = ~1.25 seconds at 8kHz)
        SILENCE_FRAMES = 100
        SILENCE_FRAME_SIZE = 160  # 20ms at 8kHz
        for _ in range(SILENCE_FRAMES):
            silence = bytes([128] * SILENCE_FRAME_SIZE)
            self.rtp.put_nowait(silence)
        
        # فقط یک بار تابع start را فراخوانی کنید
        asyncio.create_task(self.ai.start())

        self.first_packet = True
        self._send_rtp_started = False  # Track if send_rtp has been started
        
        # CRITICAL FIX: Start send_rtp() immediately, don't wait for first RTP packet
        # This ensures TTS audio is sent even if we never receive RTP from caller
        logging.info("🎤 Starting RTP send loop immediately (remote: %s:%d)", self.client_addr, self.client_port)
        asyncio.create_task(self.send_rtp())
        self._send_rtp_started = True
        
        loop = asyncio.get_running_loop()
        loop.add_reader(self.serversock.fileno(), self.read_rtp)
        logging.info("✅ RTP reader registered for socket fileno %d", self.serversock.fileno())
        logging.info("handling %s using %s AI", b2b_key, flavor)

    def bind(self, host_ip):
        """ Binds the call to a port """
        try:
            port = _port_manager.allocate_port()
            self.serversock.bind((host_ip, port))
            self._allocated_port = port
            logging.info("Bound to %s:%d", host_ip, port)
        except RuntimeError as e:
            raise NoAvailablePorts(str(e))

    def get_body(self):
        """ Retrieves the SDP built """
        return str(self.sdp)

    def get_new_sdp(self, sdp, host_ip):
        """ Gets a new SDP to be sent back in 200 OK """
        # استفاده از آدرس IP از config (برای NAT traversal)
        public_ip = rtp_cfg.get('public_ip', 'RTP_PUBLIC_IP', host_ip)
        
        sdp.origin = f"{sdp.origin.rsplit(' ', 1)[0]} {public_ip}"
        sdp.media[0].port = self.serversock.getsockname()[1]    
        sdp.host = public_ip
        sdp.media[0].host = public_ip
    
        # update SDP to return only chosen codec
        # as we do not accept anything else
        # Remove all other codecs
        sdp.media[0].rtp.codecs = [self.codec.params]
        sdp.media[0].fmt = [self.codec.payload_type]
    
        logging.info("Created SDP with IP %s and port %d", public_ip, sdp.media[0].port)
        return sdp

    def resume(self):
        """ Resumes the call's audio """
        if not self.paused:
            return
        logging.info("resuming %s", self.b2b_key)
        self.paused = False
        self.sdp.media[0].direction = "sendrecv"

    def pause(self):
        """ Pauses the call's audio """
        if self.paused:
            return
        logging.info("pausing %s", self.b2b_key)
        self.sdp.media[0].direction = "recvonly"
        self.paused = True

    def read_rtp(self):
        """ Reads a RTP packet """
    
        try:
            data, adr = self.serversock.recvfrom(4096)
            # Always log RTP packets at INFO level for debugging (throttled after first few)
            if not hasattr(self, '_rtp_packet_count'):
                self._rtp_packet_count = 0
            self._rtp_packet_count += 1
            
            # Log RTP RX packets at DEBUG level only (too verbose for INFO)
            logging.debug("📦 RTP RX: got %d bytes from %s:%d (packet #%d)", 
                        len(data), adr[0], adr[1], self._rtp_packet_count)
    
            if self.first_packet:
                self.first_packet = False
                # Update client address from actual RTP packet (more reliable than SDP)
                old_addr = (self.client_addr, self.client_port)
                self.client_addr = adr[0]
                self.client_port = adr[1]
                logging.info("🎯 First RTP packet received, client: %s:%d (was: %s:%d)", 
                           self.client_addr, self.client_port, old_addr[0], old_addr[1])
                
                # If send_rtp wasn't started yet, start it now (shouldn't happen, but safety check)
                if not self._send_rtp_started:
                    logging.warning("⚠️  send_rtp not started yet, starting now from first packet")
                    asyncio.create_task(self.send_rtp())
                    self._send_rtp_started = True
    
            if adr[0] != self.client_addr or adr[1] != self.client_port:
                logging.warning("⚠️  Ignoring RTP from unexpected source: %s:%d (expected: %s:%d)", 
                              adr[0], adr[1], self.client_addr, self.client_port)
                return
        except socket.timeout as e:
            logging.debug("RTP receive timeout: %s", e)
            return
        except Exception as e:
            logging.error("❌ RTP receive error: %s", e, exc_info=True)
            return
    
        # Drop requests if paused
        if self.paused:
            logging.debug("⏸️  RTP paused, dropping packet")
            return
            
        try:
            packet = decode_rtp_packet(data.hex())
            audio = bytes.fromhex(packet['payload'])
            
            # Log decoded RTP packets at DEBUG level only (too verbose for INFO)
            logging.debug("🎵 Decoded RTP packet #%d: %d bytes audio payload", 
                        self._rtp_packet_count, len(audio))
            
            # ارسال صدا به AI engine (که به Soniox می‌فرستد)
            asyncio.create_task(self.ai.send(audio))
        except ValueError as e:
            logging.error("❌ Error decoding RTP packet: %s", e)
        except Exception as e:
            logging.error("❌ Error processing RTP: %s", e, exc_info=True)

    async def send_rtp(self):
        """ Sends all RTP packet """

        sequence_number = random.randint(0, 10000)
        timestamp = random.randint(0, 10000)
        ssrc = random.randint(0, 2**31)
        ts_inc = self.codec.ts_increment
        ptime = self.codec.ptime
        payload_type = self.codec.payload_type
        marker = 1
        packet_no = 0
        start_time = datetime.datetime.now()
        
        logging.info("🎤 Starting RTP stream to client %s:%d", self.client_addr, self.client_port)
        logging.info("🎤 RTP socket: %s:%d", self.serversock.getsockname()[0], self.serversock.getsockname()[1])
        
        while not self.stop_event.is_set():
            try:
                payload = self.rtp.get_nowait()
                # Log RTP TX packets at DEBUG level only (too verbose for INFO)
                logging.debug("📤 RTP TX: sending TTS packet #%d (%d bytes) to %s:%d", 
                            packet_no, len(payload), self.client_addr, self.client_port)
            except Empty:
                if self.terminated:
                    logging.info("Call terminated after %d packets", packet_no)
                    self.terminate()
                    return
                if not self.paused:
                    payload = self.codec.get_silence()
                    if packet_no < 5:
                        logging.debug("No audio data, sending silence packet #%d", packet_no)
                else:
                    payload = None
                    await asyncio.sleep(0.02)  # کاهش مصرف CPU
                    continue
                    
            if payload:
                rtp_packet = generate_rtp_packet({
                    'version': 2,
                    'padding': 0,
                    'extension': 0,
                    'csi_count': 0,
                    'marker': marker,
                    'payload_type': payload_type,
                    'sequence_number': sequence_number,
                    'timestamp': timestamp,
                    'ssrc': ssrc,
                    'payload': payload.hex()
                })
                marker = 0
                sequence_number += 1
                try:
                    bytes_sent = self.serversock.sendto(bytes.fromhex(rtp_packet),
                                           (self.client_addr, self.client_port))
                    # Log RTP TX sent at DEBUG level only (too verbose for INFO)
                    logging.debug("✅ RTP TX: sent %d bytes to %s:%d (packet #%d)", 
                                bytes_sent, self.client_addr, self.client_port, packet_no)
                except Exception as e:
                    logging.error("❌ Error sending RTP packet #%d to %s:%d: %s", 
                                packet_no, self.client_addr, self.client_port, e)

            timestamp += ts_inc
            packet_no += 1
            next_time = start_time + datetime.timedelta(milliseconds=ptime *
                                                        packet_no)
            now = datetime.datetime.now()
            drift = (next_time - now).total_seconds()
            if drift > 0:
                await asyncio.sleep(float(drift))

    async def close(self):
        """ Closes the call """
        logging.info("Call %s closing", self.b2b_key)
        loop = asyncio.get_running_loop()
        loop.remove_reader(self.serversock.fileno())
        
        # Release port back to pool
        if hasattr(self, '_allocated_port'):
            _port_manager.release_port(self._allocated_port)
        else:
            # Fallback: try to get port from socket
            try:
                free_port = self.serversock.getsockname()[1]
                _port_manager.release_port(free_port)
            except Exception:
                pass
        
        self.serversock.close()
        self.stop_event.set()
        await self.ai.close()

    def terminate(self):
        """ Terminates the call """
        logging.info("Terminating call %s", self.b2b_key)
        try:
            self.mi_conn.execute("ua_session_terminate", {"key": self.b2b_key})
            asyncio.create_task(self.close())
        except Exception as e:
            logging.error("Error terminating call: %s", e)

# vim: tabstop=8 expandtab shiftwidth=4 softtabstop=4
