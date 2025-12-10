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

rtp_cfg = Config.get("rtp")
min_rtp_port = int(rtp_cfg.get("min_port", "RTP_MIN_PORT", "35000"))
max_rtp_port = int(rtp_cfg.get("max_port", "RTP_MAX_PORT", "65000"))

available_ports = set(range(min_rtp_port, max_rtp_port))


class NoAvailablePorts(Exception):
    """ There are no available ports """


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

        self.sdp = self.get_new_sdp(sdp, rtp_ip)
        
        # پخش چند فریم سکوت در ابتدا
        for _ in range(100):
            silence = bytes([128] * 160)
            self.rtp.put_nowait(silence)
        
        # فقط یک بار تابع start را فراخوانی کنید
        asyncio.create_task(self.ai.start())

        self.first_packet = True
        # Start real-time RTP reading loop (async)
        asyncio.create_task(self._read_rtp_loop())
        logging.info("handling %s using %s AI", b2b_key, flavor)

    def bind(self, host_ip):
        """ Binds the call to a port """
        if not available_ports:
            raise NoAvailablePorts()
        port = secrets.choice(list(available_ports))
        available_ports.remove(port)
        self.serversock.bind((host_ip, port))
        logging.info("Bound to %s:%d", host_ip, port)

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

    async def _read_rtp_loop(self):
        """Real-time async loop for reading RTP packets using loop.add_reader."""
        loop = asyncio.get_event_loop()
        self.serversock.setblocking(False)  # Make socket non-blocking
        
        # Queue to pass packets from sync callback to async loop
        packet_queue = asyncio.Queue(maxsize=50)  # Limit queue to prevent memory issues
        
        def _read_rtp_callback():
            """Synchronous callback for loop.add_reader - reads available packets."""
            try:
                while True:
                    try:
                        data, adr = self.serversock.recvfrom(4096)
                        try:
                            packet_queue.put_nowait((data, adr))
                        except asyncio.QueueFull:
                            # Queue full - drop oldest packet to keep real-time
                            try:
                                packet_queue.get_nowait()
                                packet_queue.put_nowait((data, adr))
                            except asyncio.QueueEmpty:
                                pass
                    except BlockingIOError:
                        # No more data available - expected for non-blocking socket
                        break
                    except OSError as e:
                        if e.errno == 11:  # EAGAIN/EWOULDBLOCK
                            break
                        else:
                            logging.error("RTP socket error: %s", e)
                            break
            except Exception as e:
                logging.error("Error in RTP reader callback: %s", e)
        
        # Register reader callback
        loop.add_reader(self.serversock.fileno(), _read_rtp_callback)
        
        try:
            while not self.terminated:
                try:
                    # Wait for packet with short timeout
                    data, adr = await asyncio.wait_for(packet_queue.get(), timeout=0.01)
                    
                    # Process first packet
                    if self.first_packet:
                        self.first_packet = False
                        self.client_addr = adr[0]
                        self.client_port = adr[1]
                        logging.info("First RTP packet received, client: %s:%d", self.client_addr, self.client_port)
                        asyncio.create_task(self.send_rtp())
                    
                    # Validate source
                    if adr[0] != self.client_addr or adr[1] != self.client_port:
                        continue
                    
                    # Drop requests if paused
                    if self.paused:
                        continue
                    
                    # Process packet immediately (REAL-TIME)
                    try:
                        packet = decode_rtp_packet(data.hex())
                        audio = bytes.fromhex(packet['payload'])
                        
                        # Send audio to Soniox immediately - await for real-time processing
                        await self.ai.send(audio)
                    except ValueError as e:
                        logging.error("Error decoding RTP packet: %s", e)
                    except Exception as e:
                        logging.error("Error processing RTP: %s", e)
                        
                except asyncio.TimeoutError:
                    # Timeout expected - continue to check terminated flag
                    continue
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    if not self.terminated:
                        logging.error("RTP receive error: %s", e)
        finally:
            # Clean up: remove reader
            loop.remove_reader(self.serversock.fileno())
    
    def read_rtp(self):
        """Legacy synchronous method - replaced by _read_rtp_loop for real-time processing."""
        # This method is kept for compatibility but not used
        pass

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
        
        logging.info("Starting RTP stream to client %s:%d", self.client_addr, self.client_port)
        
        while not self.stop_event.is_set():
            try:
                payload = self.rtp.get_nowait()
                logging.debug("Sending RTP packet: %d bytes", len(payload))
            except Empty:
                if self.terminated:
                    logging.info("Call terminated after %d packets", packet_no)
                    self.terminate()
                    return
                if not self.paused:
                    payload = self.codec.get_silence()
                    logging.debug("No audio data, sending silence")
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
                    logging.debug("Sent %d bytes to %s:%d", bytes_sent, self.client_addr, self.client_port)
                except Exception as e:
                    logging.error("Error sending RTP: %s", e)

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
        free_port = self.serversock.getsockname()[1]
        self.serversock.close()
        available_ports.add(free_port)
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
