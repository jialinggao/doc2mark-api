import pytest
from unittest.mock import Mock, patch
from app.services.ocr_service import OCRService, ocr_service
from PIL import Image
import io


class TestOCRService:
    
    def test_is_valid_ocr_result_empty_text(self):
        assert ocr_service._is_valid_ocr_result("") == False
        assert ocr_service._is_valid_ocr_result("   ") == False
    
    def test_is_valid_ocr_result_short_text(self):
        assert ocr_service._is_valid_ocr_result("ab|90.00") == False
        assert ocr_service._is_valid_ocr_result("你好啊|90.00") == True
    
    def test_is_valid_ocr_result_with_confidence(self):
        assert ocr_service._is_valid_ocr_result("Hello World|85.50") == True
        assert ocr_service._is_valid_ocr_result("Test|50.00") == False
    
    def test_is_reasonable_text_chinese(self):
        assert ocr_service._is_reasonable_text("这是一个测试文本") == True
    
    def test_is_reasonable_text_english(self):
        assert ocr_service._is_reasonable_text("This is a test text") == True
    
    def test_is_reasonable_text_mixed(self):
        assert ocr_service._is_reasonable_text("测试 Test 123") == True
    
    def test_is_reasonable_text_too_many_symbols(self):
        assert ocr_service._is_reasonable_text("@#$%^&*()!@#") == False
    
    def test_clean_ocr_text(self):
        raw_text = "Hello World|90.00\nTest Line|85.50"
        cleaned = ocr_service.clean_ocr_text(raw_text)
        assert cleaned == "Hello World\nTest Line"
    
    def test_clean_ocr_text_empty(self):
        assert ocr_service.clean_ocr_text("") == ""
    
    @patch('app.services.ocr_service.pytesseract.image_to_data')
    def test_extract_text_from_image_success(self, mock_image_to_data):
        mock_image_to_data.return_value = {
            'text': ['Hello', 'World', '', 'Test'],
            'conf': [90.0, 85.0, 0, 88.0],
            'block_num': [1, 1, 1, 1],
            'par_num': [1, 1, 1, 1],
            'line_num': [1, 1, 2, 2]
        }
        
        img = Image.new('RGB', (100, 100), color='white')
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG')
        
        result = ocr_service.extract_text_from_image(img_bytes.getvalue())
        
        assert result is not None
        assert 'Hello' in result or 'World' in result or 'Test' in result
    
    @patch('app.services.ocr_service.pytesseract.image_to_data')
    def test_extract_text_from_image_no_valid_text(self, mock_image_to_data):
        mock_image_to_data.return_value = {
            'text': ['', '', ''],
            'conf': [0, 0, 0],
            'block_num': [1, 1, 1],
            'par_num': [1, 1, 1],
            'line_num': [1, 1, 1]
        }
        
        img = Image.new('RGB', (100, 100), color='white')
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG')
        
        result = ocr_service.extract_text_from_image(img_bytes.getvalue())
        
        assert result is None
