from typing import BinaryIO
import os
from loguru import logger
from app.models import ImageMode
from app.config import settings
from app.services.excel_converter import excel_converter
from app.services.pdf_converter import pdf_converter
from app.services.word_converter import word_converter
from app.services.general_converter import general_converter
from app.services.ofd_converter import ofd_converter


class DocumentConverterService:
    """
    文档转换服务 - 路由入口

    主要功能：
    1. 根据文件扩展名路由到对应的专用转换器
    2. 提供统一的转换接口
    3. 处理转换失败的回退逻辑

    路由逻辑：
    - .xls, .xlsx → ExcelConverter (专用解析器)
    - .pdf → PdfConverter (PyMuPDF 专用处理) 或 PdfStructureConverter (PP-StructureV3，实验性)
    - .doc, .docx → WordConverter (支持自定义列表格式)
    - 其他格式 → GeneralConverter (MarkItDown 通用处理)
    """

    def convert(
        self,
        file_stream: BinaryIO,
        filename: str,
        enable_ocr: bool = False,
        enable_llm: bool = False,
        image_mode: ImageMode = ImageMode.BASE64,
        image_quality: int = 100,
        max_image_size: int = -1,
    ) -> dict:
        """
        转换文档为 Markdown 格式（统一入口）

        Args:
            file_stream: 文件二进制流
            filename: 文件名（用于识别文件类型）
            enable_ocr: 是否启用 OCR（图片转文字）
            enable_llm: 是否启用 LLM（AI 增强）
            image_mode: 图片输出模式（BASE64 或 URL）
            image_quality: 图片压缩质量（0-100）
            max_image_size: 图片最大尺寸限制（像素），-1 表示不限制

        Returns:
            转换结果字典，包含：
            - filename: 原文件名
            - markdown: Markdown 文本
            - images: 图片信息列表
            - duration: 转换耗时（秒）
        """
        # 获取文件扩展名并转为小写
        ext = os.path.splitext(filename.lower())[1]

        # Excel 文件路由
        if ext in ('.xls', '.xlsx'):
            logger.info("[Converter] 路由: Excel → excel_converter, filename={}", filename)
            return excel_converter.convert(file_stream, filename, enable_ocr, enable_llm, image_mode, image_quality, max_image_size)

        # PDF 文件路由
        elif ext == '.pdf':
            if settings.USE_STRUCTURE_ENGINE:
                logger.info("[Converter] 路由: PDF → pdf_structure_converter (PP-StructureV3), filename={}", filename)
                from app.services.pdf_structure_converter import pdf_structure_converter
                return pdf_structure_converter.convert(file_stream, filename, enable_ocr, enable_llm, image_mode, image_quality, max_image_size)
            else:
                logger.info("[Converter] 路由: PDF → pdf_converter (PyMuPDF), filename={}", filename)
                return pdf_converter.convert(file_stream, filename, enable_ocr, enable_llm, image_mode, image_quality, max_image_size)

        # OFD 文件路由（中国自主文档格式标准）
        elif ext == '.ofd':
            logger.info("[Converter] 路由: OFD → ofd_converter, filename={}", filename)
            return ofd_converter.convert(file_stream, filename, enable_ocr, enable_llm, image_mode, image_quality, max_image_size)

        # Word 文件路由（支持 .doc 和 .docx）
        elif ext in ('.doc', '.docx'):
            logger.info("[Converter] 路由: Word → word_converter, filename={}", filename)
            return word_converter.convert(file_stream, filename, enable_ocr, enable_llm, image_mode, image_quality, max_image_size)

        # 其他格式路由（包含 .ppt, .pptx, .png, .jpg, .html 等）
        else:
            logger.info("[Converter] 路由: 其他 → general_converter, filename={}", filename)
            return general_converter.convert(file_stream, filename, enable_ocr, enable_llm, image_mode, image_quality, max_image_size)


# 文档转换服务单例实例
converter_service = DocumentConverterService()