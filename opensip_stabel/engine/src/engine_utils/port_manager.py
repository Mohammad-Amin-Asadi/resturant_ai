"""
Port manager for managing RTP port allocation.
"""

import logging
import secrets
from typing import Set

logger = logging.getLogger(__name__)


class PortManager:
    """Manages available RTP ports"""
    
    def __init__(self, min_port: int, max_port: int):
        """
        Initialize port manager.
        
        Args:
            min_port: Minimum port number
            max_port: Maximum port number (exclusive)
        """
        self._available_ports: Set[int] = set(range(min_port, max_port))
        self._min_port = min_port
        self._max_port = max_port
        logger.info("PortManager initialized: ports %d-%d (%d total)", 
                   min_port, max_port - 1, len(self._available_ports))
    
    def allocate_port(self) -> int:
        """
        Allocate a random available port.
        
        Returns:
            Allocated port number
            
        Raises:
            RuntimeError: If no ports are available
        """
        if not self._available_ports:
            raise RuntimeError(f"No available ports in range {self._min_port}-{self._max_port}")
        
        port = secrets.choice(list(self._available_ports))
        self._available_ports.remove(port)
        logger.debug("Allocated port %d (%d remaining)", port, len(self._available_ports))
        return port
    
    def release_port(self, port: int) -> None:
        """
        Release a port back to the pool.
        
        Args:
            port: Port number to release
        """
        if port < self._min_port or port >= self._max_port:
            logger.warning("Port %d is outside valid range (%d-%d), ignoring", 
                         port, self._min_port, self._max_port - 1)
            return
        
        if port in self._available_ports:
            logger.warning("Port %d was already available", port)
            return
        
        self._available_ports.add(port)
        logger.debug("Released port %d (%d available)", port, len(self._available_ports))
    
    def get_available_count(self) -> int:
        """Get number of available ports"""
        return len(self._available_ports)
    
    def is_available(self, port: int) -> bool:
        """Check if a port is available"""
        return port in self._available_ports
