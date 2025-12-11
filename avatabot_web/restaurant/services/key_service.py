"""
Key management service for encryption keys.
"""

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class KeyService:
    """Service for managing encryption keys"""
    
    @staticmethod
    def get_or_create_public_key() -> Tuple[str, bool]:
        """
        Get an available public key or create a new one.
        
        Returns:
            Tuple of (public_key, is_new)
        """
        try:
            from shared.models import InscriptionModel
        except ImportError:
            from Reservation_Module.models import InscriptionModel
        
        from restaurant.services.encryption_service import EncryptionService
        
        inscription = InscriptionModel.objects.filter(use_count__lt=15).first()
        
        if inscription:
            inscription.use_count += 1
            inscription.save()
            return inscription.public_key, False
        else:
            private_key, public_key = EncryptionService.generate_keys()
            new_inscription = InscriptionModel(
                private_key=private_key,
                public_key=public_key,
                use_count=1
            )
            new_inscription.save()
            return public_key, True
    
    @staticmethod
    def get_private_key_by_public(public_key: str) -> Optional[str]:
        """
        Get private key by public key.
        
        Args:
            public_key: Public key (PEM format)
            
        Returns:
            Private key or None
        """
        try:
            from shared.models import InscriptionModel
        except ImportError:
            from Reservation_Module.models import InscriptionModel
        
        try:
            inscription = InscriptionModel.objects.filter(public_key=public_key).first()
            return inscription.private_key if inscription else None
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid input for get_private_key_by_public: {e}", exc_info=True)
            return None
        except Exception as e:
            # Catch-all for unexpected database errors
            logger.error(f"Unexpected error getting private key: {e}", exc_info=True)
            return None
