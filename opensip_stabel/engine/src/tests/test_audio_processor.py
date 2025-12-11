"""
Unit tests for audio_processor module.
"""

import unittest
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

from openai_engine.audio_processor import AudioProcessor


class AudioProcessorTestCase(unittest.TestCase):
    """Test cases for AudioProcessor"""
    
    def test_upsample_even_length(self):
        """Test upsampling with even-length input (typical 160 bytes = 80 samples)"""
        # Create 80 samples (160 bytes of PCM16)
        samples = np.arange(80, dtype=np.int16)
        pcm_data = samples.tobytes()
        
        upsampled_bytes = AudioProcessor.upsample_audio(pcm_data, from_rate=8000, to_rate=16000)
        upsampled = np.frombuffer(upsampled_bytes, dtype=np.int16)
        
        # Should double the length: 80 -> 160 samples
        self.assertEqual(upsampled.shape[0], 160)
        # Original samples should be at even indices
        np.testing.assert_array_equal(upsampled[0::2], samples)
    
    @unittest.skipIf(not HAS_NUMPY, "NumPy not available")
    def test_upsample_odd_length(self):
        """Test upsampling with odd-length input (159 bytes = 79.5 samples, but we'll test 79 samples)"""
        # Create 79 samples (158 bytes of PCM16, but we'll use 79 samples = 158 bytes)
        # Actually, for 159 bytes we need 79.5 samples, but let's test with 79 samples (158 bytes)
        # Or better: test with actual 159 bytes scenario
        # 159 bytes / 2 = 79.5, so we'll have 79 full samples + 1 byte
        # But numpy frombuffer will handle it as 79 samples (truncates)
        # Let's test with 79 samples explicitly
        samples = np.arange(79, dtype=np.int16)
        pcm_data = samples.tobytes()
        
        upsampled_bytes = AudioProcessor.upsample_audio(pcm_data, from_rate=8000, to_rate=16000)
        upsampled = np.frombuffer(upsampled_bytes, dtype=np.int16)
        
        # Should double the length: 79 -> 158 samples
        self.assertEqual(upsampled.shape[0], 158)
        # Original samples should be at even indices
        np.testing.assert_array_equal(upsampled[0::2], samples)
    
    def test_upsample_159_bytes_scenario(self):
        """Test upsampling with 159 bytes input (real-world scenario from logs)"""
        # 159 bytes = 79.5 samples, but frombuffer will give us 79 samples (truncates last byte)
        # Create 79 samples (158 bytes)
        samples = np.arange(79, dtype=np.int16)
        # Add one extra byte to simulate 159 bytes
        pcm_data = samples.tobytes() + b'\x00'
        
        # This should not crash - frombuffer will handle the extra byte
        upsampled_bytes = AudioProcessor.upsample_audio(pcm_data, from_rate=8000, to_rate=16000)
        upsampled = np.frombuffer(upsampled_bytes, dtype=np.int16)
        
        # Should work without ValueError
        self.assertGreater(upsampled.shape[0], 0)
    
    @unittest.skipIf(not HAS_NUMPY, "NumPy not available")
    def test_upsample_single_sample(self):
        """Test upsampling with single sample (n=1)"""
        samples = np.array([1000], dtype=np.int16)
        pcm_data = samples.tobytes()
        
        upsampled_bytes = AudioProcessor.upsample_audio(pcm_data, from_rate=8000, to_rate=16000)
        upsampled = np.frombuffer(upsampled_bytes, dtype=np.int16)
        
        # Should double: 1 -> 2 samples
        self.assertEqual(upsampled.shape[0], 2)
        self.assertEqual(upsampled[0], 1000)
        self.assertEqual(upsampled[1], 1000)  # Duplicated
    
    @unittest.skipIf(not HAS_NUMPY, "NumPy not available")
    def test_upsample_empty(self):
        """Test upsampling with empty input"""
        pcm_data = b''
        
        result = AudioProcessor.upsample_audio(pcm_data, from_rate=8000, to_rate=16000)
        
        # Should return empty bytes without crashing
        self.assertEqual(result, b'')
    
    @unittest.skipIf(not HAS_NUMPY, "NumPy not available")
    def test_upsample_same_rate(self):
        """Test upsampling with same rate (should return original)"""
        samples = np.arange(80, dtype=np.int16)
        pcm_data = samples.tobytes()
        
        result = AudioProcessor.upsample_audio(pcm_data, from_rate=8000, to_rate=8000)
        
        # Should return original data
        self.assertEqual(result, pcm_data)
    
    @unittest.skipIf(not HAS_NUMPY, "NumPy not available")
    def test_upsample_160_samples(self):
        """Test upsampling with exactly 160 samples (320 bytes)"""
        samples = np.arange(160, dtype=np.int16)
        pcm_data = samples.tobytes()
        
        upsampled_bytes = AudioProcessor.upsample_audio(pcm_data, from_rate=8000, to_rate=16000)
        upsampled = np.frombuffer(upsampled_bytes, dtype=np.int16)
        
        # Should double: 160 -> 320 samples
        self.assertEqual(upsampled.shape[0], 320)
        # Original samples should be at even indices
        np.testing.assert_array_equal(upsampled[0::2], samples)


if __name__ == '__main__':
    unittest.main()
