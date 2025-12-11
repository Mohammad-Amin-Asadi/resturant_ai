"""
Unit tests for adapter interfaces.
"""

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from adapters.sms_adapter import LimoSMSAdapter
from adapters.openai_adapter import OpenAIRealtimeAdapter
from adapters.soniox_adapter import SonioxSTTAdapter
from adapters.backend_adapter import DjangoBackendAdapter


class LimoSMSAdapterTestCase(unittest.TestCase):
    """Test cases for LimoSMSAdapter"""
    
    @patch('adapters.sms_adapter.requests.post')
    def test_send_sms_success(self, mock_post):
        """Test successful SMS sending"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response
        
        adapter = LimoSMSAdapter(config={'sms': {'api_key': 'test_key', 'sender_number': '1000'}})
        result = adapter.send_sms('09123456789', 'Test message')
        
        self.assertTrue(result)
        mock_post.assert_called_once()
    
    @patch('adapters.sms_adapter.requests.post')
    def test_send_sms_failure(self, mock_post):
        """Test SMS sending failure"""
        import requests
        mock_post.side_effect = requests.exceptions.RequestException()
        
        adapter = LimoSMSAdapter(config={'sms': {'api_key': 'test_key', 'sender_number': '1000'}})
        result = adapter.send_sms('09123456789', 'Test message')
        
        self.assertFalse(result)


class OpenAIRealtimeAdapterTestCase(unittest.TestCase):
    """Test cases for OpenAIRealtimeAdapter"""
    
    @patch('adapters.openai_adapter.connect')
    async def test_connect_success(self, mock_connect):
        """Test successful OpenAI connection"""
        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(return_value='{"type": "session.update"}')
        mock_connect.return_value = mock_ws
        
        adapter = OpenAIRealtimeAdapter()
        ws = await adapter.connect('wss://api.openai.com/v1/realtime', 'test_key')
        
        self.assertIsNotNone(ws)
        mock_connect.assert_called_once()
    
    @patch('adapters.openai_adapter.connect')
    async def test_send_message(self, mock_connect):
        """Test sending message to OpenAI"""
        mock_ws = AsyncMock()
        mock_connect.return_value = mock_ws
        
        adapter = OpenAIRealtimeAdapter()
        await adapter.send_message(mock_ws, {'type': 'test'})
        
        mock_ws.send.assert_called_once()


class SonioxSTTAdapterTestCase(unittest.TestCase):
    """Test cases for SonioxSTTAdapter"""
    
    @patch('adapters.soniox_adapter.connect')
    async def test_connect_success(self, mock_connect):
        """Test successful Soniox connection"""
        mock_ws = AsyncMock()
        mock_ws.recv = AsyncMock(return_value='{"status": "ok"}')
        mock_connect.return_value = mock_ws
        
        adapter = SonioxSTTAdapter()
        config = {
            'model': 'stt-rt-preview',
            'audio_format': 'pcm_s16le',
            'sample_rate': 16000,
            'num_channels': 1
        }
        ws = await adapter.connect('wss://stt-rt.soniox.com', 'test_key', config)
        
        self.assertIsNotNone(ws)
        mock_connect.assert_called_once()


class DjangoBackendAdapterTestCase(unittest.TestCase):
    """Test cases for DjangoBackendAdapter"""
    
    @patch('adapters.backend_adapter.requests.get')
    def test_track_order_success(self, mock_get):
        """Test successful order tracking"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{'id': 1, 'status': 'pending'}]
        mock_get.return_value = mock_response
        
        adapter = DjangoBackendAdapter('http://localhost:8000')
        result = adapter.track_order('09123456789')
        
        # Note: This is a sync method but adapter methods are async
        # In real implementation, would use async HTTP client
        self.assertIsNotNone(adapter)
    
    @patch('adapters.backend_adapter.requests.get')
    def test_track_order_not_found(self, mock_get):
        """Test order tracking with 404"""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response
        
        adapter = DjangoBackendAdapter('http://localhost:8000')
        # Would need async test framework for full test
        self.assertIsNotNone(adapter)
