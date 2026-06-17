from markitdown import MarkItDown
from openai import OpenAI
from app.config import settings
from app.models import ImageMode
from app.services.ocr_service import ocr_service
from app.services.llm_service import llm_service
from app.services.docx_list_parser import docx_list_parser
from docx import Document
from typing import Optional, BinaryIO, Tuple, List, Dict
from PIL import Image
import base64
import io
import re
import time
import os
import tempfile
import subprocess
import fitz
from loguru import logger


OLD_TO_NEW_FORMAT = {
    ".doc": ".docx",
    ".ppt": ".pptx",
    ".xls": ".xlsx",
}


class DocumentConverterService:
    """文档转换服务，负责将各种格式的文档转换为 Markdown 文本，并支持图片提取、OCR识别和LLM描述"""

    def __init__(self):
        """初始化文档转换服务，创建 MarkItDown 实例"""
        self.md = self._initialize_markitdown()
    
    def _initialize_markitdown(self) -> MarkItDown:
        """初始化 MarkItDown 实例，根据配置决定是否启用 LLM 增强模式
        
        Returns:
            MarkItDown: 配置好的 MarkItDown 转换器实例
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
        """文档转换入口方法，将文件流转换为 Markdown 格式

        对于旧版 Office 格式（.doc/.ppt/.xls），会先自动转换为新版格式再处理。

        Args:
            file_stream: 文件二进制流
            filename: 文件名（含扩展名），用于判断文件类型
            enable_ocr: 是否启用 OCR 图片文字识别，默认 False
            enable_llm: 是否启用 LLM 图片描述，默认 False
            image_mode: 图片输出模式（BASE64/PLACEHOLDER/EXTERNAL），默认 BASE64
            image_quality: JPEG 图片压缩质量（1-100），默认 100 不压缩
            max_image_size: 图片最大尺寸（像素），超过则等比缩放，-1 表示不限制

        Returns:
            dict: 包含 filename、markdown、images、duration 的转换结果字典
        """
        start_time = time.time()
        
        ext = os.path.splitext(filename.lower())[1]
        if ext in OLD_TO_NEW_FORMAT:
            new_ext = OLD_TO_NEW_FORMAT[ext]
            new_filename = filename[:-len(ext)] + new_ext
            logger.info(f"[格式转换] 检测到旧版 Office 格式 {ext}，自动转换为 {new_ext}")
            
            try:
                converted_stream = self._convert_old_office_format(file_stream, filename, new_ext)
                return self._convert_markdown(
                    converted_stream, new_filename, enable_ocr, enable_llm,
                    image_mode, image_quality, max_image_size, start_time
                )
            except Exception as e:
                logger.error(f"[格式转换] 转换失败: {e}")
                raise RuntimeError(f"旧版 Office 格式转换失败: {e}")
        
        return self._convert_markdown(
            file_stream, filename, enable_ocr, enable_llm,
            image_mode, image_quality, max_image_size, start_time
        )
    
    def _convert_old_office_format(self, file_stream: BinaryIO, filename: str, new_ext: str) -> BinaryIO:
        """将旧版 Office 格式文件转换为新版格式（如 .doc -> .docx）
        
        通过调用 LibreOffice 命令行工具实现格式转换。
        
        Args:
            file_stream: 原始文件二进制流
            filename: 原始文件名
            new_ext: 目标扩展名（如 .docx、.pptx、.xlsx）
            
        Returns:
            BinaryIO: 转换后的新版格式文件流
            
        Raises:
            RuntimeError: LibreOffice 转换失败或输出文件不存在时抛出
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            input_path = os.path.join(tmpdir, filename)
            with open(input_path, "wb") as f:
                f.write(file_stream.read())
            
            cmd = [
                "libreoffice", "--headless", "--convert-to", new_ext.lstrip("."),
                "--outdir", tmpdir, input_path
            ]
            
            logger.info(f"[格式转换] 执行命令: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            
            if result.returncode != 0:
                raise RuntimeError(f"LibreOffice 转换失败: {result.stderr}")
            
            base_name = os.path.splitext(filename)[0]
            output_path = os.path.join(tmpdir, base_name + new_ext)
            
            if not os.path.exists(output_path):
                raise RuntimeError(f"转换后的文件不存在: {output_path}")
            
            with open(output_path, "rb") as f:
                return io.BytesIO(f.read())
    
    def _convert_markdown(
        self,
        file_stream: BinaryIO,
        filename: str,
        enable_ocr: bool,
        enable_llm: bool,
        image_mode: ImageMode,
        image_quality: int,
        max_image_size: int,
        start_time: float
    ) -> dict:
        """核心转换逻辑：将文件流转为 Markdown，处理图片、OCR 和 LLM 描述
        
        对于 DOCX 文件，会先解析列表格式；对于 PDF 文件，委托给 PyMuPDF 处理；
        其他格式使用 MarkItDown 进行转换。转换后提取内嵌图片并按需进行 OCR 识别、
        LLM 描述和图片模式格式化。
        
        Args:
            file_stream: 文件二进制流
            filename: 文件名
            enable_ocr: 是否启用 OCR 识别
            enable_llm: 是否启用 LLM 图片描述
            image_mode: 图片输出模式
            image_quality: 图片压缩质量
            max_image_size: 图片最大尺寸限制
            start_time: 转换开始时间戳，用于计算耗时
            
        Returns:
            dict: 包含 filename、markdown、images、duration 的转换结果字典
        """
        custom_list_items = []
        
        docx_list_items = []
        if filename.lower().endswith('.docx'):
            try:
                file_stream.seek(0)
                doc = Document(file_stream)
                docx_list_items = docx_list_parser.extract_all_list_items(doc)
                
                if len(docx_list_items) == 0:
                    docx_list_items = docx_list_parser.extract_list_items_with_style(doc)
                
                file_stream.seek(0)
            except Exception as e:
                logger.warning(f"[DOCX 列表解析] 解析失败: {e}")
                docx_list_items = []
        
        if filename.lower().endswith('.pdf'):
            # 使用 PyMuPDF 处理 PDF，避免重复字符问题
            result = self._convert_pdf_with_fitz(
                file_stream, filename, enable_ocr, enable_llm, image_mode, image_quality, max_image_size
            )
            return result
        
        # 使用 MarkItDown 处理其他格式
        result = self.md.convert(file_stream, file_path=filename, keep_data_uris=True)
        
        raw_markdown = result.text_content
        
        if docx_list_items:
            raw_markdown = self._restore_custom_list_format(raw_markdown, docx_list_items)
        
        raw_markdown = self._clean_word_special_chars(raw_markdown)
        
        # 从 Markdown 中提取所有 base64 图片
        image_pattern = r'!\[([^\]]*)\]\(data:image/(\w+);base64,([A-Za-z0-9+/=]+)\)'
        image_matches = re.findall(image_pattern, raw_markdown)
        
        markdown_text = raw_markdown
        images = []
        
        # 处理每张图片
        for idx, (alt_text, img_type, base64_data) in enumerate(image_matches):
            img_name = f"image_{idx + 1}.{img_type}"
            img_data = base64.b64decode(base64_data)
            
            # 如果需要压缩，则处理图片
            if max_image_size > 0 or image_quality < 100:
                processed_bytes, img_ext = self._compress_image(img_data, image_quality, max_image_size)
            else:
                processed_bytes = img_data
                img_ext = img_type.lower()
            
            # 构建图片信息
            image_info = {
                "name": img_name,
                "content": f"data:image/{img_ext};base64,{base64.b64encode(processed_bytes).decode()}",
                "width": 0,
                "height": 0
            }
            
            # 获取图片尺寸
            try:
                img = Image.open(io.BytesIO(processed_bytes))
                image_info["width"] = img.width
                image_info["height"] = img.height
            except:
                pass
            
            images.append(image_info)
            
            # 构建替换内容
            image_block = ""
            
            # 1. OCR 识别
            if enable_ocr and settings.OCR_ENABLED:
                ocr_text = ocr_service.extract_text_from_image(processed_bytes)
                if ocr_text:
                    clean_text = ocr_service.clean_ocr_text(ocr_text)
                    image_block += f"**[OCR 识别 - {img_name}]**\n\n{clean_text}\n\n"
                else:
                    image_block += f"**[OCR 识别 - {img_name}]**\n\n（未识别出有效文字）\n\n"
            
            # 2. LLM 描述
            if enable_llm and settings.ENABLE_LLM:
                llm_desc = llm_service.describe_image(io.BytesIO(processed_bytes), f"image/{img_ext}")
                if llm_desc:
                    image_block += f"**[LLM 描述 - {img_name}]**\n\n{llm_desc}\n\n"
                else:
                    image_block += f"**[LLM 描述 - {img_name}]**\n\n（描述生成失败）\n\n"
            
            # 3. 图片输出
            image_block += self._format_image_by_mode(img_name, image_mode, image_info)
            
            # 替换原图片占位符
            original_base64_uri = f"![{alt_text}](data:image/{img_type};base64,{base64_data})"
            markdown_text = markdown_text.replace(original_base64_uri, image_block, 1)
        
        duration = time.time() - start_time
        
        return {
            "filename": filename,
            "markdown": markdown_text,
            "images": images,
            "duration": round(duration, 2)
        }
    
    def _compress_image(self, image_data: bytes, quality: int = 100, max_size: int = -1) -> tuple:
        """压缩图片：根据质量参数和最大尺寸限制进行处理

        对于 JPEG 格式按 quality 参数压缩；对于 PNG 格式使用最高压缩级别；
        如果指定了 max_size，则等比缩放使最长边不超过该值。

        Args:
            image_data: 原始图片字节数据
            quality: JPEG 压缩质量（1-100），默认 100 不压缩
            max_size: 图片最大边长（像素），-1 表示不限制缩放

        Returns:
            tuple: (处理后的图片字节数据, 图片格式字符串) 如 (bytes, 'jpeg')
        """
        try:
            img = Image.open(io.BytesIO(image_data))
            
            if max_size > 0:
                width, height = img.size
                if max(width, height) > max_size:
                    ratio = max_size / max(width, height)
                    img = img.resize((int(width * ratio), int(height * ratio)), Image.Resampling.LANCZOS)
            
            output_buffer = io.BytesIO()
            img_format = img.format or 'PNG'
            
            if img_format == 'JPEG':
                img.save(output_buffer, format=img_format, quality=quality)
            elif img_format == 'PNG':
                img.save(output_buffer, format=img_format, compress_level=9)  # PNG最高压缩级别
            else:
                img.save(output_buffer, format=img_format)
            
            return output_buffer.getvalue(), img_format.lower()
        
        except Exception as e:
            logger.error(f"压缩图片失败: {e}")
            return image_data, 'png'
    
    def _fallback_pdf_to_images(
        self,
        file_stream: BinaryIO,
        filename: str,
        image_mode: ImageMode,
        image_quality: int = 100,
        max_image_size: int = -1,
        enable_ocr: bool = False,
        enable_llm: bool = False
    ) -> dict:
        """当 MarkItDown 无法提取 PDF 内容时的回退方案：将每页转为图片
        
        将 PDF 每一页渲染为灰度 PNG 图片（150 DPI），以减小体积。
        可选地进行 OCR 识别和 LLM 描述。
        
        Args:
            file_stream: PDF 文件二进制流
            filename: 文件名
            image_mode: 图片输出模式
            image_quality: 图片压缩质量，默认 100
            max_image_size: 图片最大尺寸限制，-1 表示不限制
            enable_ocr: 是否启用 OCR 识别，默认 False
            enable_llm: 是否启用 LLM 描述，默认 False
            
        Returns:
            dict: 包含 filename、markdown、images、duration 的转换结果字典
        """
        start_time = time.time()
        images = []
        markdown_text = ""
        
        try:
            file_stream.seek(0)
            doc = fitz.open("pdf", file_stream.read())
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                
                # 将页面渲染为图片（参考原始PDF的高效存储策略：灰度 PNG）
                pix = page.get_pixmap(dpi=150, colorspace=fitz.csGRAY)  # 灰度模式，大幅减小体积
                img_data = pix.tobytes("png")  # PNG 对灰度文字压缩效率极高
                
                # PNG 已高效压缩，保持质量
                processed_bytes, img_ext = self._compress_image(img_data, 100, max_image_size)
                
                img_name = f"page_{page_num + 1}.png"
                image_info = {
                    "name": img_name,
                    "content": f"data:image/{img_ext};base64,{base64.b64encode(processed_bytes).decode()}",
                    "width": pix.width,
                    "height": pix.height
                }
                images.append(image_info)
                
                # 构建图片块
                image_block = f"## 第 {page_num + 1} 页\n\n"
                
                if enable_ocr and settings.OCR_ENABLED:
                    ocr_text = ocr_service.extract_text_from_image(processed_bytes)
                    if ocr_text:
                        clean_text = ocr_service.clean_ocr_text(ocr_text)
                        image_block += f"**[OCR 识别]**\n\n{clean_text}\n\n"
                    else:
                        image_block += f"**[OCR 识别]**\n\n（未识别出有效文字）\n\n"
                
                if enable_llm and settings.ENABLE_LLM:
                    llm_desc = llm_service.describe_image(io.BytesIO(processed_bytes), f"image/{img_ext}")
                    if llm_desc:
                        image_block += f"**[LLM 描述]**\n\n{llm_desc}\n\n"
                    else:
                        image_block += f"**[LLM 描述]**\n\n（描述生成失败）\n\n"
                
                image_block += self._format_image_by_mode(img_name, image_mode, image_info)
                
                if markdown_text:
                    markdown_text += "\n\n---\n\n"
                markdown_text += image_block
                
                logger.info(f"[PDF 回退处理] 已处理第 {page_num + 1} 页")
            
            doc.close()
            
        except Exception as e:
            logger.error(f"PDF 回退处理失败: {e}")
            return {
                "filename": filename,
                "markdown": f"处理 PDF 文件时出错: {str(e)}",
                "images": [],
                "duration": round(time.time() - start_time, 2)
            }
        
        duration = time.time() - start_time
        logger.info(f"[PDF 回退处理] 完成，共 {len(images)} 页，耗时 {duration:.2f} 秒")
        
        return {
            "filename": filename,
            "markdown": markdown_text,
            "images": images,
            "duration": round(duration, 2)
        }
    
    def _process_pdf_with_ocr(
        self,
        file_stream: BinaryIO,
        filename: str,
        markdown_text: str,
        image_mode: ImageMode,
        image_quality: int = 100,
        max_image_size: int = -1,
        enable_llm: bool = False,
        enable_ocr: bool = False
    ) -> tuple:
        """处理 PDF 中的内嵌图片：提取、压缩，并可选进行 OCR/LLM 处理

        遍历 PDF 每一页的所有内嵌图片，提取后按需压缩，
        并根据启用选项添加 OCR 识别结果或 LLM 描述，
        最后将图片块均匀插入到已有 Markdown 文本中。

        Args:
            file_stream: PDF 文件二进制流
            filename: 文件名
            markdown_text: 已有的 Markdown 文本（图片块将被插入其中）
            image_mode: 图片输出模式
            image_quality: 图片压缩质量
            max_image_size: 图片最大尺寸限制
            enable_llm: 是否启用 LLM 描述
            enable_ocr: 是否启用 OCR 识别

        Returns:
            tuple: (处理后的 Markdown 文本, 图片信息列表)
        """
        images = []
        
        try:
            file_stream.seek(0)
            doc = fitz.open("pdf", file_stream.read())
            
            image_blocks = {}
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                image_list = page.get_images(full=True)
                
                for img_index, img in enumerate(image_list):
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    img_data = base_image["image"]
                    img_name = f"page_{page_num + 1}_image_{img_index + 1}.png"
                    
                    processed_bytes, img_ext = self._compress_image(img_data, image_quality, max_image_size)
                    
                    image_info = {
                        "name": img_name,
                        "content": f"data:image/{img_ext};base64,{base64.b64encode(processed_bytes).decode()}",
                        "width": base_image.get("width", 0),
                        "height": base_image.get("height", 0)
                    }
                    
                    images.append(image_info)
                    
                    # 如果启用了OCR或LLM，生成描述
                    image_block = ""
                    
                    if enable_ocr and settings.OCR_ENABLED:
                        ocr_text = ocr_service.extract_text_from_image(processed_bytes)
                        if ocr_text:
                            clean_text = ocr_service.clean_ocr_text(ocr_text)
                            image_block += f"**[OCR 识别 - {img_name}]**\n\n{clean_text}\n\n"
                        else:
                            image_block += f"**[OCR 识别 - {img_name}]**\n\n（未识别出有效文字）\n\n"
                    
                    if enable_llm and settings.ENABLE_LLM:
                        llm_desc = llm_service.describe_image(io.BytesIO(processed_bytes), f"image/{img_ext}")
                        if llm_desc:
                            image_block += f"**[LLM 描述 - {img_name}]**\n\n{llm_desc}\n\n"
                        else:
                            image_block += f"**[LLM 描述 - {img_name}]**\n\n（描述生成失败）\n\n"
                    
                    image_block += self._format_image_by_mode(img_name, image_mode, image_info)
                    image_blocks[len(image_blocks)] = image_block
            
            doc.close()
            
            if image_blocks:
                markdown_text = self._insert_image_blocks(markdown_text, list(image_blocks.values()))
        
        except Exception as e:
            logger.error(f"处理 PDF 图片时出错: {e}")
        
        return markdown_text, images
    
    def _insert_image_blocks(self, markdown_text: str, image_blocks: list) -> str:
        """将图片块均匀插入到 Markdown 文本的段落之间
        
        将 Markdown 按双换行分段落，然后在段落之间均匀分配插入图片块，
        使图片与文本内容交替出现，避免图片集中堆叠在文末。
        
        Args:
            markdown_text: 原始 Markdown 文本
            image_blocks: 待插入的图片块列表（每个元素为一段 Markdown 格式图片描述）
            
        Returns:
            str: 插入图片后的 Markdown 文本
        """
        paragraphs = re.split(r'\n\n+', markdown_text.strip())
        
        if len(paragraphs) <= 1:
            if markdown_text:
                return markdown_text + "\n\n---\n\n" + "\n\n---\n\n".join(image_blocks)
            else:
                return "\n\n---\n\n".join(image_blocks)
        
        num_paragraphs = len(paragraphs)
        num_images = len(image_blocks)
        
        result = []
        image_idx = 0
        
        for i, paragraph in enumerate(paragraphs):
            result.append(paragraph)
            
            if i < num_paragraphs - 1 and image_idx < num_images:
                images_remaining = num_images - image_idx
                paragraphs_remaining = num_paragraphs - i - 1
                images_to_insert = max(1, images_remaining // paragraphs_remaining)
                
                for _ in range(min(images_to_insert, images_remaining)):
                    result.append("---")
                    result.append(image_blocks[image_idx])
                    image_idx += 1
        
        if image_idx < num_images:
            result.append("---")
            result.append("\n\n---\n\n".join(image_blocks[image_idx:]))
        
        return "\n\n".join(result)
    
    def _format_image_by_mode(
        self,
        img_name: str,
        image_mode: ImageMode,
        image_info: dict
    ) -> str:
        """根据图片输出模式生成对应格式的图片 Markdown 表示

        Args:
            img_name: 图片名称
            image_mode: 图片输出模式（BASE64 嵌入/PLACEHOLDER 占位/EXTERNAL 外部引用）
            image_info: 图片信息字典，包含 base64 content 等

        Returns:
            str: 格式化后的图片 Markdown 文本
        """
        if image_mode == ImageMode.BASE64:
            return f"**[图片 - {img_name}]**\n\n![{img_name}]({image_info['content']})\n\n"
        
        elif image_mode == ImageMode.PLACEHOLDER:
            return f"**[图片 - {img_name}]**\n\n[图片：{img_name}]\n\n"
        
        elif image_mode == ImageMode.EXTERNAL:
            return f"**[图片 - {img_name}]**\n\n![{img_name}](images/{img_name})\n\n"
        
        return ""
    
    def _convert_pdf_with_fitz(
        self,
        file_stream: BinaryIO,
        filename: str,
        enable_ocr: bool = False,
        enable_llm: bool = False,
        image_mode: ImageMode = ImageMode.BASE64,
        image_quality: int = 100,
        max_image_size: int = -1
    ) -> dict:
        """使用 PyMuPDF (fitz) 转换 PDF 文件，自动处理重复字符问题
        
        对于 PDF 文件不使用 MarkItDown，而是直接通过 PyMuPDF 提取文本和表格，
        并处理重复字符（PDF 渲染重叠问题）。支持：
        - 基于坐标排序的文本提取，保留阅读顺序
        - 表格识别并转为 Markdown 表格格式
        - 页码过滤（底部10%区域的纯数字文本）
        - 重叠文本片段合并与加粗标记
        - 内嵌图片提取与 OCR/LLM 处理
        
        Args:
            file_stream: PDF 文件二进制流
            filename: 文件名
            enable_ocr: 是否启用 OCR 识别，默认 False
            enable_llm: 是否启用 LLM 描述，默认 False
            image_mode: 图片输出模式，默认 BASE64
            image_quality: 图片压缩质量，默认 100
            max_image_size: 图片最大尺寸限制，-1 表示不限制
            
        Returns:
            dict: 包含 filename、markdown、images、duration 的转换结果字典
        """
        start_time = time.time()
        images = []
        markdown_text = ""
        
        try:
            file_stream.seek(0)
            doc = fitz.open("pdf", file_stream.read())
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                
                # 使用dict模式提取文本，包含完整结构信息（包括表格）
                text_dict = page.get_text("dict")
                
                page_text = ""
                
                # 用于存储所有内容（文本行和表格），按位置排序
                page_contents = []
                
                # 获取页面高度，用于判断页码位置
                page_rect = page.rect
                page_height = page_rect.height
                # 底部10%区域认为是页码区域
                footer_threshold = page_height * 0.9
                
                # 使用page.find_tables()提取表格（更准确的表格识别）
                # 收集表格的边界框，用于后续过滤重复文本
                table_bboxes = []
                tables = page.find_tables()
                for table in tables:
                    table_data = table.extract()
                    if table_data:
                        md_table = "\n"
                        for row_idx, row in enumerate(table_data):
                            md_row = "|"
                            for cell in row:
                                cell_text = str(cell).strip() if cell else ""
                                cell_text = self._remove_duplicate_chars(cell_text)
                                md_row += f" {cell_text} |"
                            md_row += "\n"
                            md_table += md_row
                            
                            if row_idx == 0 and len(row) > 0:
                                md_table += "|" + " --- |" * len(row) + "\n"
                        
                        # 获取表格的起始y坐标
                        table_y0 = table.bbox[1] if hasattr(table, 'bbox') else 0
                        page_contents.append({
                            'type': 'table',
                            'y0': table_y0,
                            'content': md_table
                        })
                        # 记录表格边界框，用于过滤重复文本
                        if hasattr(table, 'bbox'):
                            table_bboxes.append(table.bbox)
                
                # 判断文本块是否在表格区域内
                def is_in_table(block_bbox):
                    """判断给定的文本块边界框是否与任何表格边界框重叠

                    Args:
                        block_bbox: 文本块边界框 [x0, y0, x1, y1]

                    Returns:
                        bool: 是否在表格区域内
                    """
                    for t_bbox in table_bboxes:
                        # 检查是否有重叠
                        if (block_bbox[0] < t_bbox[2] and 
                            block_bbox[2] > t_bbox[0] and 
                            block_bbox[1] < t_bbox[3] and 
                            block_bbox[3] > t_bbox[1]):
                            return True
                    return False
                
                # 遍历每个文本块
                for block in text_dict.get("blocks", []):
                    if block["type"] == 0:  # text block
                        # 检查是否在表格区域内，如果是则跳过（避免重复）
                        block_bbox = block.get("bbox", [0, 0, 0, 0])
                        if is_in_table(block_bbox):
                            continue
                        # 收集所有片段（不再按精确y坐标分组）
                        all_fragments = []
                        
                        # 遍历每行
                        for line in block.get("lines", []):
                            # 遍历每个单词
                            for span in line.get("spans", []):
                                text = span.get("text", "")
                                if not text.strip():
                                    continue
                                
                                # 获取完整边界框信息
                                # bbox = [x0, y0, x1, y1]
                                # x0, y0: 左上角（起始位置）
                                # x1, y1: 右下角（结束位置）
                                bbox = span.get("bbox", [0, 0, 0, 0])
                                x0, y0, x1, y1 = bbox[0], bbox[1], bbox[2], bbox[3]
                                
                                # 获取字体信息（用于判断加粗）
                                font = span.get("font", "")
                                is_bold_font = "Bold" in font or "bold" in font or "BOLD" in font
                                
                                # 初始化加粗范围：如果字体本身加粗，则整个文本加粗
                                bold_ranges = [(0, len(text))] if is_bold_font else []
                                
                                all_fragments.append({
                                    'text': text,
                                    'x0': x0,
                                    'x1': x1,
                                    'y0': y0,
                                    'bold_ranges': bold_ranges
                                })
                        
                        # 按y坐标排序，然后按x坐标排序
                        all_fragments.sort(key=lambda f: (f['y0'], f['x0']))
                        
                        # 合并同一行的片段（y坐标接近的认为在同一行）
                        if all_fragments:
                            # 对每行的片段进行合并处理
                            merged_fragments = []
                            for frag in all_fragments:
                                if not merged_fragments:
                                    # 第一个片段，直接添加
                                    merged_fragments.append(frag.copy())
                                else:
                                    # 获取最后一个已合并的片段
                                    last = merged_fragments[-1]
                                    last_text = last['text']
                                    last_x0 = last['x0']
                                    last_x1 = last.get('x1', last_x0 + 100)  # 结束x坐标
                                    last_y0 = last['y0']
                                    last_bold_ranges = last['bold_ranges']
                                    
                                    curr_text = frag['text']
                                    curr_x0 = frag['x0']
                                    curr_x1 = frag.get('x1', curr_x0 + 100)
                                    curr_y0 = frag['y0']
                                    curr_bold_ranges = frag['bold_ranges']
                                    
                                    # 判断是否在同一行（y坐标接近，容差10像素）
                                    same_line = abs(curr_y0 - last_y0) < 10
                                    
                                    # 检查内容重叠：当前片段前部或全部与上一片段后部或全部有重叠
                                    overlap_found = False
                                    max_overlap = 0
                                    # 检查上一片段的结尾与当前片段的开头是否有重叠
                                    for i in range(1, min(len(curr_text), len(last_text)) + 1):
                                        if last_text[-i:] == curr_text[:i]:
                                            overlap_found = True
                                            max_overlap = i
                                            break
                                    
                                    # 检查边框重叠：两个片段的x范围是否有重叠
                                    # last: [last_x0, last_x1]
                                    # curr: [curr_x0, curr_x1]
                                    border_overlap = last_x0 < curr_x1 and curr_x0 < last_x1
                                    
                                    # 检查当前片段是否在上一片段的左边（需要前置合并）
                                    curr_on_left = curr_x0 < last_x0
                                    
                                    # 如果当前片段在左边，检查当前片段的结尾是否与上一片段的开头有重叠
                                    prefix_overlap = False
                                    prefix_overlap_len = 0
                                    if curr_on_left and same_line:
                                        for i in range(1, min(len(curr_text), len(last_text)) + 1):
                                            if curr_text[-i:] == last_text[:i]:
                                                prefix_overlap = True
                                                prefix_overlap_len = i
                                                break
                                    
                                    # 不在同一行，直接作为新片段
                                    if not same_line:
                                        merged_fragments.append(frag.copy())
                                    # 情况1：内容完全相同（重复绘制）- 标记整个文本为加粗
                                    elif curr_text == last_text:
                                        # 整个文本都是重复的，标记为加粗
                                        last['bold_ranges'] = [(0, len(last_text))]
                                    # 情况2：当前片段在左边且有前缀重叠，前置合并
                                    elif curr_on_left and prefix_overlap:
                                        merged_text = curr_text + last_text[prefix_overlap_len:]
                                        last['text'] = merged_text
                                        last['x0'] = curr_x0
                                        # 标记重叠部分（prefix_overlap_len）为加粗
                                        # 重叠部分在curr_text的末尾和last_text的开头
                                        overlap_start = len(curr_text) - prefix_overlap_len
                                        overlap_end = len(curr_text)
                                        last['bold_ranges'] = [(overlap_start, overlap_end)]
                                    # 情况3：内容重叠且边框重叠，合并它们
                                    elif overlap_found and border_overlap:
                                        merged_text = last_text + curr_text[max_overlap:]
                                        last['text'] = merged_text
                                        # 更新合并后的边框范围（取最小x0和最大x1）
                                        last['x0'] = min(last_x0, curr_x0)
                                        last['x1'] = max(last_x1, curr_x1)
                                        # 标记重叠部分（max_overlap）为加粗
                                        # 重叠部分在last_text的末尾和curr_text的开头
                                        overlap_start = len(last_text) - max_overlap
                                        overlap_end = len(last_text)
                                        last['bold_ranges'] = [(overlap_start, overlap_end)]
                                    # 情况4：当前片段是上一片段的子集，且边框重叠，标记为重复
                                    elif curr_text in last_text and border_overlap:
                                        # 找到curr_text在last_text中的位置
                                        start_pos = last_text.find(curr_text)
                                        end_pos = start_pos + len(curr_text)
                                        # 标记这个范围为加粗
                                        last['bold_ranges'] = [(start_pos, end_pos)]
                                    # 情况5：上一片段是当前片段的子集（内容完全包含），替换为更完整的内容
                                    elif last_text in curr_text:
                                        last['text'] = curr_text
                                        # 更新边框范围
                                        last['x0'] = min(last_x0, curr_x0)
                                        last['x1'] = max(last_x1, curr_x1)
                                        # 找到last_text在curr_text中的位置
                                        start_pos = curr_text.find(last_text)
                                        end_pos = start_pos + len(last_text)
                                        # 标记这个范围为加粗
                                        last['bold_ranges'] = [(start_pos, end_pos)]
                                    # 情况6：同一行内相邻的片段（位置接近但内容不重叠），拼接起来
                                    elif curr_x0 - last_x1 < 20:  # 间距小于20像素认为是相邻
                                        last['text'] = last_text + curr_text
                                        last['x1'] = curr_x1
                                        # 拼接部分不标记为加粗（除非原本就是加粗）
                                        # 合并两个片段的加粗范围，需要调整第二个片段的范围
                                        merged_bold_ranges = []
                                        for start, end in last_bold_ranges:
                                            merged_bold_ranges.append((start, end))
                                        for start, end in curr_bold_ranges:
                                            merged_bold_ranges.append((start + len(last_text), end + len(last_text)))
                                        last['bold_ranges'] = merged_bold_ranges
                                    else:
                                        # 没有重叠或边框不重叠，作为新片段，重置边框
                                        merged_fragments.append(frag.copy())
                            
                            # 将合并后的片段添加到总列表（过滤页码）
                            for frag in merged_fragments:
                                text = frag['text'].strip()
                                y0 = frag['y0']
                                # 判断是否是页码：位于底部10%区域且内容是数字或"第X页"格式
                                is_page_num = False
                                if y0 >= footer_threshold:
                                    # 匹配纯数字页码（1-3位）或"第X页"格式
                                    import re
                                    if re.match(r'^\d{1,3}$', text) or re.match(r'^第[\d一二三四五六七八九十]+页$', text):
                                        is_page_num = True
                                if not is_page_num:
                                    page_contents.append({
                                        'type': 'text',
                                        'y0': y0,
                                        'text': text,
                                        'bold_ranges': frag['bold_ranges']
                                    })
                    
                    elif block["type"] == 1:  # image block
                        # 图片块，后面单独处理
                        pass
                
                # 按y坐标排序（混合表格和文本）
                page_contents.sort(key=lambda x: x['y0'])
                formatted_lines = []
                prev_y0 = None
                line_height = 18  # 估计的行高，增大以减少误判
                
                for content in page_contents:
                    if content['type'] == 'table':
                        # 表格直接添加，不进行段落分隔判断
                        formatted_lines.append(content['content'])
                        prev_y0 = None  # 表格后重置段落判断
                    else:
                        text = content['text']
                        bold_ranges = content['bold_ranges']
                        current_y0 = content['y0']
                        
                        # 判断是否需要分段（更严格的条件，减少误判）
                        if prev_y0 is not None and current_y0 - prev_y0 > line_height * 2.5:
                            formatted_lines.append("")  # 添加空行表示段落分隔
                        
                        # 格式化标题和加粗
                        formatted_text = self._format_pdf_line(text, bold_ranges)
                        formatted_lines.append(formatted_text)
                        
                        prev_y0 = current_y0
                
                page_text += "\n".join(formatted_lines) + "\n"
                
                # 添加分页符
                if page_num < len(doc) - 1:
                    page_text += "\n---\n\n"
                
                # 处理页面中的图片
                for img_index, img in enumerate(page.get_images(full=True)):
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    img_data = base_image["image"]
                    img_ext = base_image["ext"]
                    
                    # 处理图片压缩
                    if max_image_size > 0 or image_quality < 100:
                        processed_bytes, img_ext = self._compress_image(img_data, image_quality, max_image_size)
                    else:
                        processed_bytes = img_data
                    
                    img_name = f"image_{page_num + 1}_{img_index + 1}.{img_ext}"
                    image_info = {
                        "name": img_name,
                        "content": f"data:image/{img_ext};base64,{base64.b64encode(processed_bytes).decode()}",
                        "width": 0,
                        "height": 0
                    }
                    
                    # 获取图片尺寸
                    try:
                        img_obj = Image.open(io.BytesIO(processed_bytes))
                        image_info["width"] = img_obj.width
                        image_info["height"] = img_obj.height
                    except:
                        pass
                    
                    images.append(image_info)
                    
                    # 构建图片处理块
                    image_block = ""
                    
                    if enable_ocr and settings.OCR_ENABLED:
                        ocr_text = ocr_service.extract_text_from_image(processed_bytes)
                        if ocr_text:
                            clean_text = ocr_service.clean_ocr_text(ocr_text)
                            image_block += f"**[OCR 识别 - {img_name}]**\n\n{clean_text}\n\n"
                        else:
                            image_block += f"**[OCR 识别 - {img_name}]**\n\n（未识别出有效文字）\n\n"
                    
                    if enable_llm and settings.ENABLE_LLM:
                        llm_desc = llm_service.describe_image(io.BytesIO(processed_bytes), f"image/{img_ext}")
                        if llm_desc:
                            image_block += f"**[LLM 描述 - {img_name}]**\n\n{llm_desc}\n\n"
                        else:
                            image_block += f"**[LLM 描述 - {img_name}]**\n\n（描述生成失败）\n\n"
                    
                    image_block += self._format_image_by_mode(img_name, image_mode, image_info)
                    
                    # 将图片块插入到文本中（简单处理：添加到页面文本末尾）
                    page_text += "\n\n" + image_block
                
                # 添加页面分隔
                if markdown_text:
                    markdown_text += "\n\n---\n\n"
                markdown_text += page_text
            
            doc.close()
            
        except Exception as e:
            logger.error(f"PyMuPDF 转换失败: {e}")
            return {
                "filename": filename,
                "markdown": f"处理 PDF 文件时出错: {str(e)}",
                "images": [],
                "duration": round(time.time() - start_time, 2)
            }
        
        duration = time.time() - start_time
        
        return {
            "filename": filename,
            "markdown": markdown_text,
            "images": images,
            "duration": round(duration, 2)
        }
    
    def _remove_duplicate_chars(self, text: str) -> str:
        """移除连续重复的字符（用于表格单元格内容去重）"""
        if not text:
            return text
        
        result = []
        prev_char = None
        for char in text:
            if char != prev_char:
                result.append(char)
                prev_char = char
        return ''.join(result)
    
    def _format_pdf_line(self, text: str, bold_ranges: list = None) -> str:
        """格式化 PDF 提取的单行文本，添加标题和加粗格式

        识别中文文档标题结构（第X编、第X章、第X节、第X条）并转换为对应级别的
        Markdown 标题；对非标题文本根据加粗范围添加 ** 加粗标记。

        Args:
            text: 原始文本行
            bold_ranges: 加粗范围列表，每个元素为 (start, end) 元组

        Returns:
            str: 格式化后的 Markdown 文本
        """
        if not text or not text.strip():
            return text
        
        if bold_ranges is None:
            bold_ranges = []
        
        # 篇标题：第xx篇（一级标题）
        match = re.match(r'^(第[\s]*[一二三四五六七八九十百]+[\s]*编)(.*)$', text)
        if match:
            chapter = match.group(1).strip()
            content = match.group(2).strip()
            if content:
                return f"# {chapter}\n{content}"
            else:
                return f"# {chapter}"
        
        # 章标题：第xx章（二级标题）
        match = re.match(r'^(第[\s]*[一二三四五六七八九十百]+[\s]*章)(.*)$', text)
        if match:
            chapter = match.group(1).strip()
            content = match.group(2).strip()
            if content:
                return f"## {chapter}\n{content}"
            else:
                return f"## {chapter}"
        
        # 节标题：第xx节（三级标题）
        match = re.match(r'^(第[\s]*[一二三四五六七八九十百]+[\s]*节)(.*)$', text)
        if match:
            section = match.group(1).strip()
            content = match.group(2).strip()
            if content:
                return f"### {section}\n{content}"
            else:
                return f"### {section}"
        
        # 条标题：第xx条（四级标题）
        match = re.match(r'^(第[\s]*[一二三四五六七八九十百]+[\s]*条)(.*)$', text)
        if match:
            article = match.group(1).strip()
            content = match.group(2).strip()
            if content:
                return f"#### {article}\n{content}"
            else:
                return f"#### {article}"
        
        # 其他加粗内容（不符合四级标题的），根据加粗范围添加加粗标记
        if bold_ranges:
            # 按加粗范围分割文本并添加加粗标记
            result = []
            last_end = 0
            for start, end in bold_ranges:
                # 添加非加粗部分
                if start > last_end:
                    result.append(text[last_end:start])
                # 添加加粗部分
                result.append(f"**{text[start:end]}**")
                last_end = end
            # 添加剩余的非加粗部分
            if last_end < len(text):
                result.append(text[last_end:])
            return ''.join(result)
        
        return text
    
    def _clean_word_special_chars(self, markdown: str) -> str:
        """清理 Word 文档中的特殊格式字符（PUA 字符）"""
        cleaned = markdown
        
        for code in range(0xE000, 0xF900):
            char = chr(code)
            if char in cleaned:
                cleaned = cleaned.replace(char, '')
        
        return cleaned
    
    def _restore_custom_list_format(self, markdown: str, list_items: List[Tuple[str, str]]) -> str:
        """恢复 DOCX 文档中的自定义列表格式

        将 MarkItDown 提取的 Markdown 文本中的列表项，根据原始 DOCX 解析得到的
        列表��级信息，还原为正确的缩进/层级格式。

        Args:
            markdown: MarkItDown 提取的原始 Markdown 文本
            list_items: 列表项信息列表，每个元素为 (层级文本, 原始内容) 元组

        Returns:
            str: 列表格式恢复后的 Markdown 文本
        """
        if not list_items:
            return markdown
        
        lines = markdown.split('\n')
        new_lines = []
        item_index = 0
        
        for line in lines:
            line_stripped = line.strip()
            
            if not line_stripped:
                new_lines.append(line)
                continue
            
            if item_index < len(list_items):
                lvl_text, original_content = list_items[item_index]
                content_stripped = original_content.strip()
                
                if not content_stripped:
                    new_lines.append(line)
                    continue
                
                line_clean = re.sub(r'^\d+\.\s*', '', line_stripped)
                
                content_clean = re.sub(r'[^\w\u4e00-\u9fff]', '', content_stripped)
                line_clean_for_match = re.sub(r'[^\w\u4e00-\u9fff]', '', line_clean)
                
                min_match_len = min(len(content_clean), 8)
                if min_match_len >= 4 and line_clean_for_match.startswith(content_clean[:min_match_len]):
                    new_lines.append(f'- {lvl_text} {line_clean}')
                    item_index += 1
                elif re.match(r'^\d+\.\s*', line_stripped):
                    if item_index < len(list_items):
                        lvl_text, _ = list_items[item_index]
                        new_lines.append(f'- {lvl_text} {line_clean}')
                        item_index += 1
                    else:
                        new_lines.append(line)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
        
        return '\n'.join(new_lines)


converter_service = DocumentConverterService()
