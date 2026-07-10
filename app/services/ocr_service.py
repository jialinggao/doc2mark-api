from PIL import Image
import io
import re
import numpy as np
from typing import Optional, BinaryIO
from app.config import settings
from loguru import logger

try:
    import pytesseract
except ImportError:
    logger.warning("pytesseract 未安装")

try:
    from paddleocr import PaddleOCR
    PADDLEOCR_AVAILABLE = True
except ImportError:
    PADDLEOCR_AVAILABLE = False
    logger.warning("PaddleOCR 未安装，将使用 Tesseract 作为后备")


class OCRService:
    """
    OCR服务 - 支持PaddleOCR 3.7（优先）和Tesseract（后备）
    
    主要功能：
    1. 使用PaddleOCR PP-OCRv6模型进行高精度OCR识别
    2. 支持版面分析，保持段落格式
    3. PaddleOCR不可用时自动回退到Tesseract
    4. 延迟加载：首次使用时才初始化模型
    """
    
    def __init__(self):
        """
        初始化OCR服务（延迟加载模式）
        
        优先使用PaddleOCR（中文识别精度更高），如果PaddleOCR不可用则回退到Tesseract
        """
        self.use_paddleocr = PADDLEOCR_AVAILABLE
        self.min_length = settings.OCR_MIN_LENGTH
        self.min_confidence = settings.OCR_MIN_CONFIDENCE
        self.max_symbol_ratio = settings.OCR_MAX_SYMBOL_RATIO
        
        # 延迟加载标志
        self._paddleocr_initialized = False
        self._tesseract_initialized = False
        
        # 存储模型实例
        self.ocr = None
        self.language = None
    
    def _initialize_paddleocr(self):
        """
        初始化PaddleOCR（延迟加载）
        """
        if self._paddleocr_initialized:
            return
        
        logger.info("首次使用OCR，初始化 PaddleOCR 3.7（PP-OCRv6模型）...")
        try:
            import os
            os.environ['OMP_NUM_THREADS'] = '1'
            os.environ['MKL_NUM_THREADS'] = '1'
            os.environ['FLAGS_use_mkldnn'] = 'False'
            os.environ['FLAGS_use_onednn'] = 'False'
            os.environ['FLAGS_use_mkldnn_bf16'] = 'False'
            
            # 使用PaddleOCR 3.7的正确初始化方式
            # 禁用文档预处理和方向分类，只保留文本检测和识别
            self.ocr = PaddleOCR(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False
            )
            logger.info("PaddleOCR 初始化成功")
            self._paddleocr_initialized = True
        except Exception as e:
            logger.error(f"PaddleOCR 初始化失败，将使用 Tesseract: {e}")
            import traceback
            logger.error(traceback.format_exc())
            self.use_paddleocr = False
            self._initialize_tesseract()
    
    def _initialize_tesseract(self):
        """
        初始化Tesseract（延迟加载）
        """
        if self._tesseract_initialized:
            return
        
        logger.info("首次使用OCR，初始化 Tesseract OCR")
        try:
            import pytesseract
            if settings.OCR_TESSERACT_PATH:
                pytesseract.pytesseract.tesseract_cmd = settings.OCR_TESSERACT_PATH
            self.language = settings.OCR_LANGUAGE
            logger.info("Tesseract 初始化成功")
        except ImportError:
            logger.error("Tesseract 也不可用，OCR 功能将受限")
        self._tesseract_initialized = True
    
    def _ensure_initialized(self):
        """
        确保OCR引擎已初始化
        """
        if self.use_paddleocr and not self._paddleocr_initialized:
            self._initialize_paddleocr()
        elif not self._tesseract_initialized:
            self._initialize_tesseract()
    
    def extract_text_from_image(self, image_data: bytes) -> Optional[str]:
        """
        从图片字节数据中提取文字
        
        参数:
            image_data: 图片字节数据
        
        返回:
            识别的文字内容，失败时返回None
        """
        try:
            # 确保OCR引擎已初始化
            self._ensure_initialized()
            
            image = Image.open(io.BytesIO(image_data))
            
            if self.use_paddleocr:
                return self._extract_with_paddleocr(image)
            else:
                return self._extract_with_tesseract(image)
            
        except Exception as e:
            logger.error(f"OCR 识别失败: {e}")
            return None
    
    def extract_text_from_image_file(self, image_path: str) -> Optional[str]:
        """
        从图片文件中提取文字
        
        参数:
            image_path: 图片文件路径
        
        返回:
            识别的文字内容，失败时返回None
        """
        try:
            # 确保OCR引擎已初始化
            self._ensure_initialized()
            
            image = Image.open(image_path)
            
            if self.use_paddleocr:
                return self._extract_with_paddleocr(image)
            else:
                return self._extract_with_tesseract(image)
            
        except Exception as e:
            logger.error(f"OCR 识别失败 {image_path}: {e}")
            return None
    
    def _extract_with_paddleocr(self, image: Image.Image) -> Optional[str]:
        """
        使用PaddleOCR提取文字
        
        参数:
            image: PIL图片对象
        
        返回:
            识别的文字内容（带置信度标记）
        """
        try:
            # 确保图像是 3 通道 RGB 格式
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            # 保存临时文件以便PaddleOCR处理
            import tempfile
            import os
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as temp_file:
                temp_path = temp_file.name
                image.save(temp_path, format='PNG')
            
            try:
                # 使用PaddleOCR 3.7的predict方法
                result = self.ocr.predict(temp_path)
                logger.debug(f"PaddleOCR 返回结果: {type(result)}, 值: {result}")
            finally:
                # 清理临时文件
                try:
                    os.unlink(temp_path)
                except:
                    pass
            
        except Exception as e:
            logger.error(f"PaddleOCR 识别失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return None
        
        if not result:
            logger.info("PaddleOCR 未识别出文字")
            return None
        
        # 解析 PaddleOCR 3.7 的结果，同时处理 rec_texts 和 rec_scores
        all_text = []
        
        try:
            # 处理 PaddleOCR 3.7 的结果格式
            for res_item in result:
                # 检查是否有 res 属性
                if hasattr(res_item, 'res'):
                    res_data = res_item.res
                elif isinstance(res_item, dict):
                    res_data = res_item
                else:
                    continue
                
                # 获取结果数据
                ocr_result = None
                if isinstance(res_data, dict) and 'rec_texts' in res_data:
                    ocr_result = res_data
                elif isinstance(res_data, dict) and 'res' in res_data:
                    inner_res = res_data['res']
                    if isinstance(inner_res, dict) and 'rec_texts' in inner_res:
                        ocr_result = inner_res
                
                if ocr_result:
                    rec_texts = ocr_result.get('rec_texts', [])
                    rec_scores = ocr_result.get('rec_scores', [])
                    
                    # 确保 rec_texts 和 rec_scores 都是列表
                    if isinstance(rec_texts, list) and isinstance(rec_scores, list):
                        # 遍历文本和对应的置信度
                        for i, text in enumerate(rec_texts):
                            if isinstance(text, str) and len(text.strip()) > 0:
                                confidence = 0.0
                                if i < len(rec_scores):
                                    try:
                                        confidence = float(rec_scores[i])
                                    except (ValueError, TypeError):
                                        confidence = 0.0
                                
                                # 添加带置信度标记的文本，格式与Tesseract一致
                                all_text.append(f"{text.strip()}|{confidence:.2f}")
        
        except Exception as e:
            logger.error(f"解析 PaddleOCR 结果失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        logger.debug(f"从 PaddleOCR 结果中提取到的文字: {all_text}")
        
        if not all_text:
            logger.debug("PaddleOCR 未识别出有效文字")
            return None
        
        # 拼接所有识别到的文字
        final_text = '\n'.join(all_text)
        logger.debug(f"PaddleOCR 最终识别结果: {final_text}")
        
        return final_text.strip()
    
    def _extract_with_tesseract(self, image: Image.Image) -> Optional[str]:
        """
        使用Tesseract提取文字（后备方案）
        
        参数:
            image: PIL图片对象
        
        返回:
            识别的文字内容
        """
        ocr_data = pytesseract.image_to_data(image, lang=self.language, output_type=pytesseract.Output.DICT)
        text_with_confidence = self._build_text_with_confidence(ocr_data)
        
        if not self._is_valid_ocr_result(text_with_confidence):
            logger.info("Tesseract 识别结果无效，跳过输出")
            return None
        
        return self.clean_ocr_text(text_with_confidence).strip()
    
    def _build_text_with_confidence(self, ocr_data: dict) -> str:
        """
        从Tesseract输出中构建带置信度的文本
        
        参数:
            ocr_data: Tesseract输出的字典数据
        
        返回:
            带置信度标记的文本
        """
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
        """
        验证OCR结果是否有效
        
        参数:
            text: OCR识别的文本
        
        返回:
            是否有效
        """
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
        """
        判断文本是否合理（检查符号比例）
        
        参数:
            text: 待检查的文本
        
        返回:
            是否合理
        """
        symbol_pattern = re.compile(r'[^\w\s\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]')
        symbols = symbol_pattern.findall(text)
        
        if len(text) == 0:
            return False
        
        symbol_ratio = len(symbols) / len(text)
        return symbol_ratio <= self.max_symbol_ratio
    
    def clean_ocr_text(self, text: str) -> str:
        """
        清理OCR文本（移除置信度标记）
        
        参数:
            text: 带置信度标记的文本
        
        返回:
            清理后的纯文本
        """
        lines = text.split('\n')
        clean_lines = []
        
        for line in lines:
            if '|' in line:
                # 处理带有置信度标记的行（Tesseract格式）
                parts = line.rsplit('|', 1)
                if len(parts) == 2:
                    line_text = parts[0].strip()
                    if line_text:
                        clean_lines.append(line_text)
            else:
                # 处理没有置信度标记的行（PaddleOCR格式）
                line_text = line.strip()
                if line_text:
                    clean_lines.append(line_text)
        
        return '\n'.join(clean_lines)


ocr_service = OCRService()
