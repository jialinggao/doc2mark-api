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
    def __init__(self):
        self.md = self._initialize_markitdown()
    
    def _initialize_markitdown(self) -> MarkItDown:
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
        
        # 使用 keep_data_uris=True 保留完整的 base64 图片数据
        result = self.md.convert(file_stream, file_path=filename, keep_data_uris=True)
        
        raw_markdown = result.text_content
        
        raw_markdown = self._remove_pdf_bold_duplicates(raw_markdown)
        
        if docx_list_items:
            raw_markdown = self._restore_custom_list_format(raw_markdown, docx_list_items)
        
        raw_markdown = self._clean_word_special_chars(raw_markdown)
        
        # 如果 MarkItDown 无法提取内容（如扫描件 PDF），使用 PyMuPDF 将每页转为图片
        if not raw_markdown.strip() and filename.lower().endswith('.pdf'):
            logger.info(f"[转换] MarkItDown 未提取到内容，使用 PyMuPDF 回退方案")
            return self._fallback_pdf_to_images(
                file_stream, filename, image_mode, image_quality, max_image_size, enable_ocr, enable_llm
            )
        
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
        """当 MarkItDown 无法提取 PDF 内容时的回退方案：将每页转为图片"""
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
        if image_mode == ImageMode.BASE64:
            return f"**[图片 - {img_name}]**\n\n![{img_name}]({image_info['content']})\n\n"
        
        elif image_mode == ImageMode.PLACEHOLDER:
            return f"**[图片 - {img_name}]**\n\n[图片：{img_name}]\n\n"
        
        elif image_mode == ImageMode.EXTERNAL:
            return f"**[图片 - {img_name}]**\n\n![{img_name}](images/{img_name})\n\n"
        
        return ""
    
    def _clean_word_special_chars(self, markdown: str) -> str:
        """清理 Word 文档中的特殊格式字符（PUA 字符）"""
        cleaned = markdown
        
        for code in range(0xE000, 0xF900):
            char = chr(code)
            if char in cleaned:
                cleaned = cleaned.replace(char, '')
        
        return cleaned
    
    def _remove_pdf_bold_duplicates(self, markdown: str) -> str:
        """清理PDF中由于加粗效果导致的重复字符（同一字符连续绘制多次）"""
        if not markdown:
            return markdown
        
        cleaned = []
        i = 0
        n = len(markdown)
        
        while i < n:
            current_char = markdown[i]
            cleaned.append(current_char)
            i += 1
            
            if i < n and markdown[i] == current_char:
                duplicate_count = 1
                while i + duplicate_count < n and markdown[i + duplicate_count] == current_char:
                    duplicate_count += 1
                
                if 2 <= duplicate_count <= 4:
                    i += duplicate_count - 1
        
        return ''.join(cleaned)
    
    def _restore_custom_list_format(self, markdown: str, list_items: List[Tuple[str, str]]) -> str:
        """恢复自定义列表格式"""
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
