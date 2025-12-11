"""
Encryption service for handling RSA + AES-GCM hybrid encryption.
"""

import base64
import json
import logging
from Crypto.Cipher import PKCS1_OAEP, AES
from Crypto.PublicKey import RSA

logger = logging.getLogger(__name__)


class EncryptionService:
    """Service for encryption/decryption operations"""
    
    @staticmethod
    def decrypt_data(private_key: str, encrypted_data: str) -> dict:
        """
        Hybrid decoder:
        - decode base64(JSON(package))
        - decrypt AES key with RSA private key
        - decrypt ciphertext with AES-GCM
        
        Args:
            private_key: RSA private key (PEM format)
            encrypted_data: Base64-encoded encrypted package
            
        Returns:
            Decrypted data dictionary
            
        Raises:
            ValueError: If decryption fails
        """
        try:
            package_json = base64.b64decode(encrypted_data)
            package = json.loads(package_json)
            
            enc_key = base64.b64decode(package['key'])
            nonce = base64.b64decode(package['nonce'])
            tag = base64.b64decode(package['tag'])
            ciphertext = base64.b64decode(package['ciphertext'])
            
            private_key_obj = RSA.import_key(private_key)
            cipher_rsa = PKCS1_OAEP.new(private_key_obj)
            sym_key = cipher_rsa.decrypt(enc_key)
            
            cipher_aes = AES.new(sym_key, AES.MODE_GCM, nonce=nonce)
            plaintext = cipher_aes.decrypt_and_verify(ciphertext, tag)
            
            return json.loads(plaintext.decode("utf-8"))
        except (ValueError, TypeError, KeyError) as e:
            # Invalid input data
            logger.error(f"Invalid encrypted data format: {e}", exc_info=True)
            raise ValueError(f"Invalid encrypted data: {e}")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            # Decryption succeeded but JSON decode failed
            logger.error(f"Decrypted data is not valid JSON: {e}", exc_info=True)
            raise ValueError(f"Decrypted data is not valid JSON: {e}")
        except Exception as e:
            # Cryptographic or other unexpected errors
            logger.error(f"Decryption failed: {e}", exc_info=True)
            raise ValueError(f"Decryption failed: {e}")
    
    @staticmethod
    def generate_keys() -> tuple[str, str]:
        """
        Generate RSA key pair.
        
        Returns:
            Tuple of (private_key, public_key) in PEM format
        """
        key = RSA.generate(2048)
        private_key = key.export_key().decode("utf-8")
        public_key = key.publickey().export_key().decode("utf-8")
        return private_key, public_key
