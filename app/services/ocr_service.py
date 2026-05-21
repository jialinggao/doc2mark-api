from PIL import Image
import pytesseract
import io
import re
from typing import Optional, BinaryIO
from app.config import settings
from loguru import logger


class OCRService:
    def __init__(self):
        if settings.OCR_TESSERACT_PATH:
            pytesseract.pytesseract.tesseract_cmd = settings.OCR_TESSERACT_PATH
        self.language = settings.OCR_LANGUAGE
        self.min_length = settings.OCR_MIN_LENGTH
        self.min_confidence = settings.OCR_MIN_CONFIDENCE
        self.max_symbol_ratio = settings.OCR_MAX_SYMBOL_RATIO
    
    def extract_text_from_image(self, image_data: bytes) -> Optional[str]:
        try:
            image = Image.open(io.BytesIO(image_data))
            ocr_data = pytesseract.image_to_data(image, lang=self.language, output_type=pytesseract.Output.DICT)
            
            text_with_confidence = self._build_text_with_confidence(ocr_data)
            
            if not self._is_valid_ocr_result(text_with_confidence):
                logger.info("OCR 识别结果无效，跳过输出")
                return None
            
            return text_with_confidence.strip()
        
        except Exception as e:
            logger.error(f"OCR 识别失败: {e}")
            return None
    
    def extract_text_from_image_file(self, image_path: str) -> Optional[str]:
        try:
            image = Image.open(image_path)
            ocr_data = pytesseract.image_to_data(image, lang=self.language, output_type=pytesseract.Output.DICT)
            
            text_with_confidence = self._build_text_with_confidence(ocr_data)
            
            if not self._is_valid_ocr_result(text_with_confidence):
                logger.info(f"图片 {image_path} 未识别出有效文字，跳过 OCR 输出")
                return None
            
            return text_with_confidence.strip()
        
        except Exception as e:
            logger.error(f"OCR 识别失败 {image_path}: {e}")
            return None
    
    def _build_text_with_confidence(self, ocr_data: dict) -> str:
        lines = []
        current_line = []
        current_line_confidences = []
        
        for i in range(len(ocr_data['text'])):
            text = ocr_data['text'][i].strip()
            confidence = ocr_data['conf'][i]
            
            if text and confidence > 0:
                current_line.append(text)
                current_line_confidences.append(confidence)
            
            if i < len(ocr_data['text']) - 1:
                if ocr_data['block_num'][i] != ocr_data['block_num'][i + 1] or \
                   ocr_data['par_num'][i] != ocr_data['par_num'][i + 1] or \
                   ocr_data['line_num'][i] != ocr_data['line_num'][i + 1]:
                    if current_line:
                        avg_confidence = sum(current_line_confidences) / len(current_line_confidences)
                        line_text = ' '.join(current_line)
                        lines.append(f"{line_text}|{avg_confidence:.2f}")
                        current_line = []
                        current_line_confidences = []
        
        if current_line:
            avg_confidence = sum(current_line_confidences) / len(current_line_confidences)
            line_text = ' '.join(current_line)
            lines.append(f"{line_text}|{avg_confidence:.2f}")
        
        return '\n'.join(lines)
    
    def _is_valid_ocr_result(self, text: str) -> bool:
        if not text:
            return False
        
        lines = text.split('\n')
        clean_lines = []
        
        for line in lines:
            if '|' in line:
                parts = line.rsplit('|', 1)
                if len(parts) == 2:
                    line_text = parts[0]
                    try:
                        confidence = float(parts[1])
                        if confidence < self.min_confidence * 100:
                            continue
                    except ValueError:
                        pass
                    
                    if len(line_text.strip()) < self.min_length:
                        continue
                    
                    if not self._is_reasonable_text(line_text):
                        continue
                    
                    clean_lines.append(line_text)
        
        return len(clean_lines) > 0 and len(''.join(clean_lines)) >= self.min_length
    
    def _is_reasonable_text(self, text: str) -> bool:
        symbol_pattern = re.compile(r'[^\w\s\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]')
        symbols = symbol_pattern.findall(text)
        
        if len(text) == 0:
            return False
        
        symbol_ratio = len(symbols) / len(text)
        return symbol_ratio <= self.max_symbol_ratio
    
    def clean_ocr_text(self, text: str) -> str:
        lines = text.split('\n')
        clean_lines = []
        
        for line in lines:
            if '|' in line:
                parts = line.rsplit('|', 1)
                if len(parts) == 2:
                    line_text = parts[0].strip()
                    if line_text:
                        clean_lines.append(line_text)
        
        return '\n'.join(clean_lines)


ocr_service = OCRService()
