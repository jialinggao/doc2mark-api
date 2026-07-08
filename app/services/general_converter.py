from markitdown import MarkItDown
from openai import OpenAI
from typing import BinaryIO
import time
from loguru import logger
from app.config import settings
from app.models import ImageMode
from app.services.image_processor import image_processor


def clean_word_special_chars(markdown: str) -> str:
    """
    清理 Word 文档中的特殊字符

    Word 文档常包含 Unicode 专用区 (Private Use Area, 0xE000-0xF900)
    的字符，这些字符在 Markdown 中通常没有实际意义，需要清理。

    Args:
        markdown: 原始 Markdown 文本

    Returns:
        清理后的 Markdown 文本
    """
    cleaned = markdown

    # 清理 Unicode 专用区字符 (0xE000-0xF900)
    for code in range(0xE000, 0xF900):
        char = chr(code)
        if char in cleaned:
            cleaned = cleaned.replace(char, '')

    return cleaned


class GeneralConverter:
    """
    通用文档转换器

    主要功能：
    1. 使用 MarkItDown 处理非 Word/PDF/Excel 的其他格式
    2. 支持 PowerPoint、图片、HTML 等格式
    3. 可选集成 LLM 增强转换质量
    4. 处理图片提取和转换
    5. 清理特殊字符
    """

    def __init__(self):
        """
        初始化通用转换器
        """
        self.md = self._initialize_markitdown()

    def _initialize_markitdown(self) -> MarkItDown:
        """
        初始化 MarkItDown 实例

        Returns:
            MarkItDown 对象
        """
        if settings.ENABLE_LLM:
            client = OpenAI(
                base_url=settings.LLM_BASE_URL,
                api_key=settings.LLM_API_KEY or "sk-local-model"
            )
            return MarkItDown(
                llm_client=client,
                llm_model=settings.LLM_MODEL,
                llm_prompt=settings.LLM_PROMPT
            )
        else:
            return MarkItDown()

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
        转换文档为 Markdown

        Args:
            file_stream: 文件二进制流
            filename: 文件名
            enable_ocr: 是否启用 OCR
            enable_llm: 是否启用 LLM
            image_mode: 图片输出模式
            image_quality: 图片质量
            max_image_size: 图片最大尺寸

        Returns:
            转换结果字典
        """
        start_time = time.time()

        # 使用 MarkItDown 转换
        result = self.md.convert(file_stream, file_path=filename, keep_data_uris=True)
        raw_markdown = result.text_content

        # 清理特殊字符
        raw_markdown = clean_word_special_chars(raw_markdown)

        # 处理图片
        markdown_text, images = image_processor.process_markdown_images(
            raw_markdown, image_mode, image_quality, max_image_size, enable_ocr, enable_llm
        )

        duration = time.time() - start_time

        return {
            "filename": filename,
            "markdown": markdown_text,
            "images": images,
            "duration": round(duration, 2)
        }


# 通用转换器单例实例
general_converter = GeneralConverter()
