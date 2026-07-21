"""
PDF 文档转换器 - 使用 PP-StructureV3

通过 structure_engine.py（IPC）将 PDF 转为原始 Markdown，
再交由 image_processor.py 处理图片（压缩、格式化等）。

流程：
1. 通过 Unix Socket 请求 Structure 引擎进程处理 PDF
2. 将返回的原始 Markdown 中的 base64 图片交给 image_processor.py 处理
   - OCR 固定为 False（Structure 引擎已从图片中提取文字）
   - LLM 描述由上层参数控制
"""
import time
from typing import BinaryIO, Optional
from loguru import logger
from app.models import ImageMode
from app.services.image_processor import image_processor


class PdfStructureConverter:
    """
    PDF 结构转换器 - 基于 PP-StructureV3

    通过远程 Structure 引擎进程（structure_engine.py）处理 PDF，
    返回的原始 Markdown 再经 image_processor.py 处理图片。
    """

    def __init__(self, socket_path='/tmp/structure.sock'):
        self.socket_path = socket_path

    def convert(
        self,
        file_stream: BinaryIO,
        filename: str,
        enable_ocr: bool = False,
        enable_llm: bool = False,
        image_mode: ImageMode = ImageMode.BASE64,
        image_quality: int = 100,
        max_image_size: int = -1
    ) -> dict:
        """
        使用 PP-StructureV3 转换 PDF 为 Markdown

        Args:
            file_stream: PDF 文件二进制流
            filename: 文件名
            enable_ocr: 不生效（Structure 引擎已内置 OCR）
            enable_llm: 是否启用 LLM 图片描述
            image_mode: 图片输出模式
            image_quality: 图片压缩质量
            max_image_size: 图片最大边长像素

        Returns:
            标准转换结果字典
        """
        start_time = time.time()

        # Step 1: 请求远程 Structure 引擎
        raw_result = self._remote_convert(file_stream, filename)
        if raw_result is None:
            return {
                "filename": filename,
                "markdown": "Structure 引擎不可用",
                "images": [],
                "duration": round(time.time() - start_time, 2)
            }

        # Step 2: 处理原始 Markdown 中的图片
        raw_markdown = raw_result.get("markdown", "")
        processed_md, processed_images = image_processor.process_markdown_images(
            raw_markdown, image_mode, image_quality, max_image_size,
            enable_ocr=False,
            enable_llm=enable_llm
        )

        duration = round(time.time() - start_time, 2)
        logger.info(
            "[PPStructure] 转换完成: {} ({} 张图片, {:.2f}s)",
            filename, len(processed_images), duration
        )

        return {
            "filename": filename,
            "markdown": processed_md,
            "images": processed_images,
            "duration": duration
        }

    def _remote_convert(self, file_stream: BinaryIO, filename: str) -> Optional[dict]:
        """通过 Unix Socket 请求 Structure 引擎进程，返回原始 Markdown"""
        try:
            from multiprocessing.connection import Client

            file_stream.seek(0)
            file_data = file_stream.read()

            with Client(self.socket_path, family='AF_UNIX') as conn:
                conn.send({"filename": filename})
                conn.send_bytes(file_data)
                result = conn.recv()

            result["filename"] = filename
            logger.info(
                "[PPStructure] 远程引擎返回原始结果: {} ({} 张图片, {:.2f}s)",
                filename, len(result.get("images", [])), result.get("duration", 0)
            )
            return result

        except (FileNotFoundError, ConnectionRefusedError, OSError) as e:
            logger.error("[PPStructure] 远程引擎不可用: {}", e)
            return None
        except Exception as e:
            logger.error("[PPStructure] 远程引擎通信异常: {}", e)
            return None


# 单例实例
pdf_structure_converter = PdfStructureConverter()