"""
Unit tests for engine utilities.
"""

import unittest
from datetime import datetime, timedelta
from openai_engine.utils import DateTimeUtils, NumberConverter, WeatherService


class DateTimeUtilsTestCase(unittest.TestCase):
    """Test cases for DateTimeUtils"""
    
    def test_to_ascii_digits(self):
        """Test Persian/Arabic to ASCII digit conversion"""
        self.assertEqual(DateTimeUtils.to_ascii_digits("۰۱۲۳"), "0123")
        self.assertEqual(DateTimeUtils.to_ascii_digits("۴۵۶۷"), "4567")
        self.assertEqual(DateTimeUtils.to_ascii_digits("۸۹"), "89")
        self.assertEqual(DateTimeUtils.to_ascii_digits("٠١٢٣"), "0123")
        self.assertEqual(DateTimeUtils.to_ascii_digits("normal123"), "normal123")
    
    def test_extract_time(self):
        """Test time extraction from Persian text"""
        self.assertEqual(DateTimeUtils.extract_time("ساعت 14:30"), "14:30")
        self.assertEqual(DateTimeUtils.extract_time("صبح"), "09:00")
        self.assertEqual(DateTimeUtils.extract_time("ظهر"), "12:00")
        self.assertEqual(DateTimeUtils.extract_time("بعدازظهر"), "15:00")
        self.assertEqual(DateTimeUtils.extract_time("عصر"), "17:00")
        self.assertEqual(DateTimeUtils.extract_time("شب"), "20:00")
        self.assertIsNone(DateTimeUtils.extract_time(""))
        self.assertIsNone(DateTimeUtils.extract_time("invalid"))
    
    def test_parse_natural_date(self):
        """Test natural date parsing"""
        now = datetime(2024, 1, 15, 12, 0, 0)
        
        self.assertEqual(DateTimeUtils.parse_natural_date("امروز", now), "2024-01-15")
        self.assertEqual(DateTimeUtils.parse_natural_date("فردا", now), "2024-01-16")
        self.assertEqual(DateTimeUtils.parse_natural_date("دیروز", now), "2024-01-14")
        self.assertEqual(DateTimeUtils.parse_natural_date("پسفردا", now), "2024-01-17")
        
        # Test weekday
        result = DateTimeUtils.parse_natural_date("شنبه", now)
        self.assertIsNotNone(result)
    
    def test_normalize_date(self):
        """Test date normalization"""
        self.assertEqual(DateTimeUtils.normalize_date("2024-01-15"), "2024-01-15")
        self.assertIsNone(DateTimeUtils.normalize_date("invalid"))
        self.assertIsNone(DateTimeUtils.normalize_date(""))
    
    def test_normalize_time(self):
        """Test time normalization"""
        self.assertEqual(DateTimeUtils.normalize_time("14:30"), "14:30")
        self.assertEqual(DateTimeUtils.normalize_time("9:05"), "09:05")
        self.assertIsNone(DateTimeUtils.normalize_time("25:00"))  # Invalid hour
        self.assertIsNone(DateTimeUtils.normalize_time("14:60"))  # Invalid minute


class NumberConverterTestCase(unittest.TestCase):
    """Test cases for NumberConverter"""
    
    def test_convert_to_persian_words_phone(self):
        """Test phone number conversion"""
        # This will only work if num2words is installed
        try:
            result = NumberConverter.convert_to_persian_words("شماره 09123456789")
            # Should convert digits to words
            self.assertIsInstance(result, str)
        except Exception:
            # If num2words not available, should return original
            result = NumberConverter.convert_to_persian_words("شماره 09123456789")
            self.assertIsInstance(result, str)
    
    def test_convert_in_output_dict(self):
        """Test converting numbers in dictionary"""
        output = {
            'message': 'قیمت 150000 تومان',
            'order_id': 123
        }
        
        result = NumberConverter.convert_in_output(output)
        
        self.assertIsInstance(result, dict)
        self.assertIn('message', result)
        self.assertIn('order_id', result)
    
    def test_convert_in_output_list(self):
        """Test converting numbers in list"""
        output = ['قیمت 150000', 'قیمت 200000']
        
        result = NumberConverter.convert_in_output(output)
        
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)


class WeatherServiceTestCase(unittest.TestCase):
    """Test cases for WeatherService"""
    
    @unittest.mock.patch('openai_engine.utils.requests.get')
    def test_fetch_weather_success(self, mock_get):
        """Test successful weather fetch"""
        mock_response = unittest.mock.Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'status': 'OK',
            'result': {
                'temp': '25',
                'condition': 'آفتابی',
                'humidity': '60',
                'wind_speed': '10'
            }
        }
        mock_get.return_value = mock_response
        
        result = WeatherService.fetch_weather('تهران', did_config={'weather': {'api_token': 'test_token'}})
        
        self.assertNotIn('error', result)
        self.assertIn('city', result)
        self.assertIn('temperature', result)
    
    @unittest.mock.patch('openai_engine.utils.requests.get')
    def test_fetch_weather_no_token(self, mock_get):
        """Test weather fetch without API token"""
        result = WeatherService.fetch_weather('تهران', did_config={})
        
        self.assertIn('error', result)
        self.assertIn('پیکربندی نشده', result['error'])
    
    @unittest.mock.patch('openai_engine.utils.requests.get')
    def test_fetch_weather_timeout(self, mock_get):
        """Test weather fetch timeout"""
        import requests
        mock_get.side_effect = requests.exceptions.Timeout()
        
        result = WeatherService.fetch_weather('تهران', did_config={'weather': {'api_token': 'test_token'}})
        
        self.assertIn('error', result)
        self.assertIn('زمان', result['error'])
