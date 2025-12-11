"""
Unit tests for ReservationService.
"""

from django.test import TestCase
from taxi.services.reservation_service import ReservationService

try:
    from taxi.models import ReservationModel, TaxiStatusLog
except ImportError:
    from Reservation_Module.models import ReservationModel, TaxiStatusLog


class ReservationServiceTestCase(TestCase):
    """Test cases for ReservationService"""
    
    def setUp(self):
        """Set up test data"""
        self.reservation = ReservationModel.objects.create(
            user_fullname='علی احمدی',
            origin='تهران، میدان آزادی',
            destination='تهران، میدان ولیعصر',
            status='to_source'
        )
    
    def test_create_reservation_success(self):
        """Test successful reservation creation"""
        decrypted_data = {
            'user_fullname': 'محمد رضایی',
            'origin': 'تهران، میدان آزادی',
            'destination': 'تهران، میدان ولیعصر'
        }
        
        reservation, result = ReservationService.create_reservation_from_decrypted_data(decrypted_data)
        
        self.assertIsNotNone(reservation)
        self.assertIn('success', result)
        self.assertTrue(result['success'])
        self.assertEqual(reservation.user_fullname, 'محمد رضایی')
    
    def test_update_reservation_status(self):
        """Test updating reservation status"""
        result = ReservationService.update_reservation_status(
            self.reservation.id,
            'at_source',
            old_status='to_source',
            changed_by='test_user'
        )
        
        self.assertIn('success', result)
        self.assertTrue(result['success'])
        
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.status, 'at_source')
        
        # Check log was created
        logs = TaxiStatusLog.objects.filter(reservation=self.reservation)
        self.assertEqual(logs.count(), 1)
        self.assertEqual(logs.first().old_status, 'to_source')
        self.assertEqual(logs.first().new_status, 'at_source')
    
    def test_update_reservation_status_same(self):
        """Test updating to same status"""
        result = ReservationService.update_reservation_status(
            self.reservation.id,
            'to_source',
            old_status='to_source',
            changed_by='test_user'
        )
        
        self.assertIn('success', result)
        self.assertTrue(result['success'])
        self.assertIn('تغییر نکرده', result['message'])
    
    def test_update_reservation_status_invalid(self):
        """Test updating with invalid status"""
        result = ReservationService.update_reservation_status(
            self.reservation.id,
            'invalid_status',
            changed_by='test_user'
        )
        
        self.assertIn('error', result)
        self.assertIn('نامعتبر', result['error'])
    
    def test_delete_reservation_success(self):
        """Test deleting a reservation"""
        reservation_id = self.reservation.id
        
        result = ReservationService.delete_reservation(reservation_id)
        
        self.assertIn('success', result)
        self.assertTrue(result['success'])
        self.assertFalse(ReservationModel.objects.filter(id=reservation_id).exists())
    
    def test_delete_reservation_not_found(self):
        """Test deleting non-existent reservation"""
        result = ReservationService.delete_reservation(99999)
        
        self.assertIn('error', result)
        self.assertIn('not found', result['error'])
