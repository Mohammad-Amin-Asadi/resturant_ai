"""
Reservation service for managing taxi reservations.
"""

import logging
from typing import Dict, Any, Optional
from django.db import transaction

logger = logging.getLogger(__name__)


class ReservationService:
    """Service for reservation-related operations"""
    
    @staticmethod
    def create_reservation_from_decrypted_data(decrypted_data: Dict[str, Any]) -> tuple[Any, Dict[str, Any]]:
        """
        Create a reservation from decrypted data.
        
        Args:
            decrypted_data: Decrypted reservation data dictionary
            
        Returns:
            Tuple of (reservation, result_dict)
        """
        try:
            from taxi.models import ReservationModel
            from taxi.serializers import ReservationSerializer
        except ImportError:
            from Reservation_Module.models import ReservationModel
            from Reservation_Module.serializers import ReservationSerializer
        
        serializer = ReservationSerializer(data=decrypted_data)
        if not serializer.is_valid():
            return None, {'error': serializer.errors}
        
        reservation = serializer.save()
        return reservation, {'success': True, 'reservation': serializer.data}
    
    @staticmethod
    def update_reservation_status(
        reservation_id: int,
        new_status: str,
        old_status: Optional[str] = None,
        changed_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update reservation status and log the change.
        
        Args:
            reservation_id: Reservation ID
            new_status: New status value
            old_status: Optional old status (if not provided, fetched from DB)
            changed_by: Optional user identifier
            
        Returns:
            Dictionary with update result
        """
        try:
            from taxi.models import ReservationModel, TaxiStatusLog
        except ImportError:
            from Reservation_Module.models import ReservationModel, TaxiStatusLog
        
        try:
            reservation = ReservationModel.objects.get(id=reservation_id)
        except ReservationModel.DoesNotExist:
            return {'error': 'رزرو یافت نشد'}
        
        if not old_status:
            old_status = reservation.status
        
        valid_statuses = [choice[0] for choice in ReservationModel.STATUS_CHOICES]
        if new_status not in valid_statuses:
            return {'error': f'وضعیت نامعتبر است. وضعیت‌های معتبر: {", ".join(valid_statuses)}'}
        
        if old_status == new_status:
            return {
                'success': True,
                'message': 'وضعیت تغییر نکرده است',
                'status': new_status
            }
        
        with transaction.atomic():
            reservation.status = new_status
            reservation.save()
            
            try:
                TaxiStatusLog.objects.create(
                    reservation=reservation,
                    old_status=old_status,
                    new_status=new_status,
                    changed_by=changed_by or 'system'
                )
                logger.info(f"✅ Status log created for reservation {reservation_id}")
            except (ValueError, TypeError) as e:
                logger.error(f"Invalid data for status log: {e}")
            except Exception as e:
                # Non-critical: status log creation failure shouldn't break reservation update
                logger.warning(f"Failed to create status log (non-critical): {e}")
        
        logger.info(f"✅ Reservation {reservation_id} status updated: {old_status} → {new_status}")
        
        return {
            'success': True,
            'message': f'وضعیت از {dict(ReservationModel.STATUS_CHOICES).get(old_status, old_status)} به {dict(ReservationModel.STATUS_CHOICES).get(new_status, new_status)} تغییر کرد',
            'old_status': old_status,
            'new_status': new_status
        }
    
    @staticmethod
    def delete_reservation(reservation_id: int) -> Dict[str, Any]:
        """
        Delete a reservation.
        
        Args:
            reservation_id: Reservation ID
            
        Returns:
            Dictionary with deletion result
        """
        try:
            from taxi.models import ReservationModel
        except ImportError:
            from Reservation_Module.models import ReservationModel
        
        try:
            reservation = ReservationModel.objects.get(id=reservation_id)
            reservation.delete()
            return {
                'success': True,
                'message': 'Reservation deleted successfully'
            }
        except ReservationModel.DoesNotExist:
            return {'error': 'Reservation not found'}
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid input for delete_reservation: {e}", exc_info=True)
            return {'error': 'شناسه رزرو نامعتبر است'}
        except Exception as e:
            # Catch-all for unexpected database errors
            logger.error(f"Unexpected error deleting reservation: {e}", exc_info=True)
            return {'error': f'خطا در حذف رزرو: {str(e)}'}
