"""
OCR 服务 - 客户端代理

通过 Unix Socket 连接 ocr_engine 进程，共享 PaddleOCR 实例。
调用方代码无需感知底层实现，接口保持不变。
"""
import time
from typing import Optional
from loguru import logger


class OCRService:
    """
    OCR服务 - 远程引擎客户端

    通过 Unix Socket 连接独立运行的 ocr_engine 进程，
    调用 PaddleOCR 进行图片文字识别。
    """

    def __init__(self, socket_path='/tmp/ocr.sock'):
        self.socket_path = socket_path

    def extract_text_from_image(self, image_data: bytes) -> Optional[str]:
        """
        从图片字节数据中提取文字

        Args:
            image_data: 图片字节数据

        Returns:
            识别的文字内容，失败时返回None
        """
        _t0 = time.time()
        logger.info("[OCRService.extract_text_from_image] enter: data_size={} bytes", len(image_data))
        try:
            result = self._remote_extract(image_data)
            text_len = len(result) if result else 0
            logger.info("[OCRService.extract_text_from_image] exit: text_len={}, duration={:.3f}s", text_len, time.time() - _t0)
            return result
        except Exception as e:
            logger.error("[OCRService.extract_text_from_image] exit: error={}, duration={:.3f}s", e, time.time() - _t0)
            return None

    def extract_text_from_image_file(self, image_path: str) -> Optional[str]:
        """
        从图片文件中提取文字

        Args:
            image_path: 图片文件路径

        Returns:
            识别的文字内容，失败时返回None
        """
        _t0 = time.time()
        logger.info("[OCRService.extract_text_from_image_file] enter: path={}", image_path)
        try:
            with open(image_path, 'rb') as f:
                image_data = f.read()
            result = self._remote_extract(image_data)
            text_len = len(result) if result else 0
            logger.info("[OCRService.extract_text_from_image_file] exit: text_len={}, duration={:.3f}s", text_len, time.time() - _t0)
            return result
        except Exception as e:
            logger.error("[OCRService.extract_text_from_image_file] exit: error={}, duration={:.3f}s", e, time.time() - _t0)
            return None

    def _remote_extract(self, image_data: bytes) -> Optional[str]:
        """
        通过 Unix Socket 请求 OCR 引擎进程

        Args:
            image_data: 图片字节数据

        Returns:
            识别的文本，失败返回 None
        """
        try:
            from multiprocessing.connection import Client
            with Client(self.socket_path, family='AF_UNIX') as conn:
                conn.send_bytes(image_data)
                result_bytes = conn.recv_bytes()
                if result_bytes:
                    return result_bytes.decode('utf-8')
                return None
        except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
            logger.error("[OCRService] 远程引擎不可用: {}", e)
            return None
        except Exception as e:
            logger.warning("[OCRService] 远程引擎通信异常: {}", e)
            return None

    def clean_ocr_text(self, text: str) -> str:
        """
        清理OCR文本（移除置信度标记）

        Args:
            text: 带置信度标记的文本（PaddleOCR格式 text|confidence）

        Returns:
            清理后的纯文本
        """
        _t0 = time.time()
        logger.debug("[OCRService.clean_ocr_text] enter: text_len={}", len(text))
        lines = text.split('\n')
        clean_lines = []

        for line in lines:
            if '|' in line:
                parts = line.rsplit('|', 1)
                if len(parts) == 2:
                    line_text = parts[0].strip()
                    if line_text:
                        clean_lines.append(line_text)
            else:
                line_text = line.strip()
                if line_text:
                    clean_lines.append(line_text)

        result = '\n'.join(clean_lines)
        logger.debug("[OCRService.clean_ocr_text] exit: text_len={} -> {}, duration={:.3f}s",
                      len(text), len(result), time.time() - _t0)
        return result


ocr_service = OCRService()