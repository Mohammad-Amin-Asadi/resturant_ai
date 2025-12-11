"""
Adapter interfaces for external APIs.
Provides abstraction layer for testing and swapping implementations.
"""

from .sms_adapter import SMSAdapter, LimoSMSAdapter
from .openai_adapter import OpenAIAdapter, OpenAIRealtimeAdapter
from .soniox_adapter import SonioxAdapter, SonioxSTTAdapter
from .backend_adapter import BackendAdapter, DjangoBackendAdapter

__all__ = [
    'SMSAdapter', 'LimoSMSAdapter',
    'OpenAIAdapter', 'OpenAIRealtimeAdapter',
    'SonioxAdapter', 'SonioxSTTAdapter',
    'BackendAdapter', 'DjangoBackendAdapter',
]
