"""
Audio processing utilities for OpenAI engine: codec selection, format conversion, Soniox audio processing.
"""

import logging
import audioop
import numpy as np
from codec import get_codecs, CODECS, UnsupportedCodec

try:
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

logger = logging.getLogger(__name__)


class AudioProcessor:
    """Handles audio codec selection and processing"""
    
    @staticmethod
    def choose_codec(sdp):
        """Choose appropriate codec from SDP"""
        codecs = get_codecs(sdp)
        for codec in codecs:
            if codec.name in CODECS:
                return codec
        raise UnsupportedCodec("No supported codec found")
    
    @staticmethod
    def get_codec_name(codec):
        """Get codec name for OpenAI format"""
        if codec.name == "mulaw":
            return "g711_ulaw"
        elif codec.name == "alaw":
            return "g711_alaw"
        elif codec.name == "opus":
            return "opus"
        else:
            return "g711_ulaw"
    
    @staticmethod
    def get_audio_format(codec_name):
        """Get audio format string for OpenAI"""
        return codec_name
    
    @staticmethod
    def get_soniox_audio_format():
        """Get audio format for Soniox STT"""
        return "pcm_s16le", 16000, 1
    
    @staticmethod
    def convert_g711_to_pcm16(audio_data, is_ulaw=True):
        """Convert G.711 (μ-law or A-law) to PCM16"""
        if is_ulaw:
            return audioop.ulaw2lin(audio_data, 2)
        else:
            return audioop.alaw2lin(audio_data, 2)
    
    @staticmethod
    def upsample_audio(pcm_data, from_rate=8000, to_rate=16000):
        """Upsample audio from one sample rate to another"""
        if from_rate == to_rate:
            return pcm_data
        
        if not HAS_NUMPY:
            logger.warning("NumPy not available, cannot upsample audio")
            return pcm_data
        
        try:
            # Convert bytes to numpy array
            samples = np.frombuffer(pcm_data, dtype=np.int16)
            
            # Calculate upsampling factor
            factor = to_rate / from_rate
            
            if factor == 2:
                # Simple linear interpolation for 2x upsampling
                # Convert to numpy array if not already
                samples = np.asarray(samples, dtype=np.int16)
                n = samples.shape[0]
                
                if n == 0:
                    return pcm_data
                
                upsampled = np.empty(n * 2, dtype=np.int16)
                
                # Put the original samples on even indices (0, 2, 4, ..., 2*(n-1))
                upsampled[0::2] = samples
                
                # Interpolate BETWEEN samples on odd indices.
                # There are (n - 1) gaps between n samples.
                if n > 1:
                    # Fill all interior odd positions with the average of neighbors
                    # upsampled[1:-1:2] has length (n-1), matching interpolated
                    upsampled[1:-1:2] = (samples[:-1] + samples[1:]) // 2
                    # For the last odd index (end of array), just repeat the last sample
                    upsampled[-1] = samples[-1]
                else:
                    # n == 1, just duplicate the single sample
                    upsampled[-1] = samples[0]
            else:
                # Use numpy's interpolation for other factors
                indices = np.linspace(0, len(samples) - 1, int(len(samples) * factor))
                upsampled = np.interp(indices, np.arange(len(samples)), samples.astype(np.float32))
                upsampled = upsampled.astype(np.int16)
            
            return upsampled.tobytes()
        except Exception as e:
            # Log error only once per call, not per packet
            # Use a simple approach: log at WARNING level (not ERROR) to reduce spam
            logger.warning("Error upsampling audio (returning original): %s (input length: %d bytes)", 
                          e, len(pcm_data))
            # Return original data on error to prevent complete failure
            return pcm_data
    
    @staticmethod
    def process_audio_for_soniox(audio_data, codec, soniox_upsample=True):
        """
        Process audio for Soniox STT: convert codec to PCM16 and upsample if needed.
        
        Args:
            audio_data: Raw audio bytes
            codec: Codec object
            soniox_upsample: Whether to upsample to 16kHz
            
        Returns:
            Processed audio bytes (PCM16, 16kHz, mono)
        """
        try:
            # Convert to PCM16 based on codec
            if codec.name == "mulaw":
                pcm_data = AudioProcessor.convert_g711_to_pcm16(audio_data, is_ulaw=True)
            elif codec.name == "alaw":
                pcm_data = AudioProcessor.convert_g711_to_pcm16(audio_data, is_ulaw=False)
            elif codec.name == "opus":
                # Opus decoding should be handled by codec library
                # For now, assume it's already PCM16
                pcm_data = audio_data
            else:
                # Default: assume μ-law
                pcm_data = AudioProcessor.convert_g711_to_pcm16(audio_data, is_ulaw=True)
            
            # Upsample to 16kHz if needed
            if soniox_upsample:
                codec_rate = getattr(codec, 'rate', 8000)
                if codec_rate != 16000:
                    pcm_data = AudioProcessor.upsample_audio(pcm_data, from_rate=codec_rate, to_rate=16000)
            
            return pcm_data
        except Exception as e:
            logger.error("Error processing audio for Soniox: %s (audio_data length: %d)", e, len(audio_data), exc_info=True)
            # Return original data on error to prevent complete failure
            return audio_data
