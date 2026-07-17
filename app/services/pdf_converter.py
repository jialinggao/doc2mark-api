import base64
import io
import re
import time
from typing import BinaryIO, Tuple, List, Dict
import fitz
from PIL import Image
from loguru import logger
from app.models import ImageMode
from app.config import settings
from app.services.image_processor import image_processor
from app.services.ocr_service import ocr_service
from app.services.llm_service import llm_service


class PdfConverter:
    """
    PDF 文档转换器

    主要功能：
    1. 使用 PyMuPDF (fitz) 提取 PDF 文本、表格和图片
    2. 识别表格并转换为 Markdown 格式
    3. 识别加粗文本格式
    4. 识别中文标题结构（编、章、节、条）
    5. 提取并处理 PDF 中的图片
    6. 自动过滤页码
    7. OCR 回退处理（当文本提取过少时）
    """

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
        转换 PDF 文档为 Markdown

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
        images = []
        markdown_text = ""
        extracted_text_length = 0

        try:
            file_stream.seek(0)
            # 使用 PyMuPDF 打开 PDF
            doc = fitz.open("pdf", file_stream.read())

            # 逐页处理 PDF
            for page_num in range(len(doc)):
                page = doc[page_num]

                # 获取页面文本和格式信息
                text_dict = page.get_text("dict")

                page_text = ""
                page_text_count = 0

                page_contents = []

                # 计算页脚阈值
                page_rect = page.rect
                page_height = page_rect.height
                footer_threshold = page_height * 0.9

                # 处理表格
                table_bboxes = []
                tables = page.find_tables()
                for table in tables:
                    table_data = table.extract()
                    if table_data:
                        # 转换为 Markdown 表格
                        md_table = "\n"
                        for row_idx, row in enumerate(table_data):
                            md_row = "|"
                            for cell in row:
                                cell_text = str(cell).strip() if cell else ""
                                cell_text = self._remove_duplicate_chars(cell_text)
                                md_row += f" {cell_text} |"
                            md_row += "\n"
                            md_table += md_row

                            # 添加表格分隔线
                            if row_idx == 0 and len(row) > 0:
                                md_table += "|" + " --- |" * len(row) + "\n"

                        # 记录表格位置
                        table_y0 = table.bbox[1] if hasattr(table, 'bbox') else 0
                        page_contents.append({
                            'type': 'table',
                            'y0': table_y0,
                            'content': md_table
                        })
                        if hasattr(table, 'bbox'):
                            table_bboxes.append(table.bbox)

                # 检查文本块是否在表格区域内
                def is_in_table(block_bbox):
                    for t_bbox in table_bboxes:
                        if (block_bbox[0] < t_bbox[2] and
                            block_bbox[2] > t_bbox[0] and
                            block_bbox[1] < t_bbox[3] and
                            block_bbox[3] > t_bbox[1]):
                            return True
                    return False

                # 处理文本块
                for block in text_dict.get("blocks", []):
                    if block["type"] == 0:  # 文本块
                        block_bbox = block.get("bbox", [0, 0, 0, 0])
                        # 跳过表格区域内的文本
                        if is_in_table(block_bbox):
                            continue

                        all_fragments = []

                        for line in block.get("lines", []):
                            for span in line.get("spans", []):
                                text = span.get("text", "")
                                if not text.strip():
                                    continue

                                # 获取位置和字体信息
                                bbox = span.get("bbox", [0, 0, 0, 0])
                                x0, y0, x1, y1 = bbox[0], bbox[1], bbox[2], bbox[3]

                                font = span.get("font", "")
                                is_bold_font = "Bold" in font or "bold" in font or "BOLD" in font

                                bold_ranges = [(0, len(text))] if is_bold_font else []

                                all_fragments.append({
                                    'text': text,
                                    'x0': x0,
                                    'x1': x1,
                                    'y0': y0,
                                    'bold_ranges': bold_ranges
                                })

                        # 按位置排序文本片段
                        all_fragments.sort(key=lambda f: (f['y0'], f['x0']))

                        # 合并相邻的文本片段
                        if all_fragments:
                            merged_fragments = []
                            for frag in all_fragments:
                                if not merged_fragments:
                                    merged_fragments.append(frag.copy())
                                else:
                                    last = merged_fragments[-1]
                                    last_text = last['text']
                                    last_x0 = last['x0']
                                    last_x1 = last.get('x1', last_x0 + 100)
                                    last_y0 = last['y0']
                                    last_bold_ranges = last['bold_ranges']

                                    curr_text = frag['text']
                                    curr_x0 = frag['x0']
                                    curr_x1 = frag.get('x1', curr_x0 + 100)
                                    curr_y0 = frag['y0']
                                    curr_bold_ranges = frag['bold_ranges']

                                    # 检查是否在同一行
                                    same_line = abs(curr_y0 - last_y0) < 10

                                    # 检查文本重叠
                                    overlap_found = False
                                    max_overlap = 0
                                    for i in range(1, min(len(curr_text), len(last_text)) + 1):
                                        if last_text[-i:] == curr_text[:i]:
                                            overlap_found = True
                                            max_overlap = i
                                            break

                                    border_overlap = last_x0 < curr_x1 and curr_x0 < last_x1
                                    curr_on_left = curr_x0 < last_x0

                                    # 检查前缀重叠（当前片段在左边）
                                    prefix_overlap = False
                                    prefix_overlap_len = 0
                                    if curr_on_left and same_line:
                                        for i in range(1, min(len(curr_text), len(last_text)) + 1):
                                            if curr_text[-i:] == last_text[:i]:
                                                prefix_overlap = True
                                                prefix_overlap_len = i
                                                break

                                    if not same_line:
                                        merged_fragments.append(frag.copy())
                                    elif curr_text == last_text:
                                        last['bold_ranges'] = [(0, len(last_text))]
                                    elif curr_on_left and prefix_overlap:
                                        merged_text = curr_text + last_text[prefix_overlap_len:]
                                        last['text'] = merged_text
                                        last['x0'] = curr_x0
                                        overlap_start = len(curr_text) - prefix_overlap_len
                                        overlap_end = len(curr_text)
                                        last['bold_ranges'] = [(overlap_start, overlap_end)]
                                    elif overlap_found and border_overlap:
                                        merged_text = last_text + curr_text[max_overlap:]
                                        last['text'] = merged_text
                                        last['x0'] = min(last_x0, curr_x0)
                                        last['x1'] = max(last_x1, curr_x1)
                                        overlap_start = len(last_text) - max_overlap
                                        overlap_end = len(last_text)
                                        last['bold_ranges'] = [(overlap_start, overlap_end)]
                                    elif curr_text in last_text and border_overlap:
                                        start_pos = last_text.find(curr_text)
                                        end_pos = start_pos + len(curr_text)
                                        last['bold_ranges'] = [(start_pos, end_pos)]
                                    elif last_text in curr_text:
                                        last['text'] = curr_text
                                        last['x0'] = min(last_x0, curr_x0)
                                        last['x1'] = max(last_x1, curr_x1)
                                        start_pos = curr_text.find(last_text)
                                        end_pos = start_pos + len(last_text)
                                        last['bold_ranges'] = [(start_pos, end_pos)]
                                    elif curr_x0 - last_x1 < 20:
                                        # 距离很近，直接拼接
                                        last['text'] = last_text + curr_text
                                        last['x1'] = curr_x1
                                        merged_bold_ranges = []
                                        for start, end in last_bold_ranges:
                                            merged_bold_ranges.append((start, end))
                                        for start, end in curr_bold_ranges:
                                            merged_bold_ranges.append((start + len(last_text), end + len(last_text)))
                                        last['bold_ranges'] = merged_bold_ranges
                                    else:
                                        merged_fragments.append(frag.copy())

                            # 添加合并后的文本片段
                            for frag in merged_fragments:
                                text = frag['text'].strip()
                                y0 = frag['y0']
                                is_page_num = False
                                # 检测并过滤页码
                                if y0 >= footer_threshold:
                                    if re.match(r'^\d{1,3}$', text) or re.match(r'^第[\d一二三四五六七八九十]+页$', text):
                                        is_page_num = True
                                if not is_page_num:
                                    page_contents.append({
                                        'type': 'text',
                                        'y0': y0,
                                        'text': text,
                                        'bold_ranges': frag['bold_ranges']
                                    })
                                    page_text_count += len(text)

                    elif block["type"] == 1:  # 图片块，稍后统一处理
                        pass

                # 按垂直位置排序所有内容
                page_contents.sort(key=lambda x: x['y0'])
                formatted_lines = []
                prev_y0 = None
                line_height = 18

                # 格式化页面内容
                for content in page_contents:
                    if content['type'] == 'table':
                        formatted_lines.append(content['content'])
                        prev_y0 = None
                    else:
                        text = content['text']
                        bold_ranges = content['bold_ranges']
                        current_y0 = content['y0']

                        # 段落分隔
                        if prev_y0 is not None and current_y0 - prev_y0 > line_height * 2.5:
                            formatted_lines.append("")

                        # 格式化文本行
                        formatted_text = self._format_pdf_line(text, bold_ranges)
                        formatted_lines.append(formatted_text)

                        prev_y0 = current_y0

                page_text += "\n".join(formatted_lines) + "\n"

                # 页面分隔符（非最后一页）
                if page_num < len(doc) - 1:
                    page_text += "\n---\n\n"

                # 处理页面中的图片
                for img_index, img in enumerate(page.get_images(full=True)):
                    xref = img[0]
                    base_image = doc.extract_image(xref)
                    img_data = base_image["image"]
                    img_ext = base_image["ext"]

                    # 压缩图片（如果需要）
                    if max_image_size > 0 or image_quality < 100:
                        processed_bytes, img_ext = image_processor.compress_image(img_data, image_quality, max_image_size)
                    else:
                        processed_bytes = img_data

                    img_name = f"image_{page_num + 1}_{img_index + 1}.{img_ext}"

                    # 构建图片块
                    image_block, image_info = image_processor.build_image_block(
                        img_name, processed_bytes, img_ext, image_mode, enable_ocr, enable_llm
                    )

                    if image_info:
                        images.append(image_info)
                    page_text += "\n\n" + image_block

                # 添加到最终 Markdown
                if markdown_text:
                    markdown_text += "\n\n---\n\n"
                markdown_text += page_text
                extracted_text_length += page_text_count

            doc.close()

        except Exception as e:
            logger.error(f"PyMuPDF 转换失败: {e}")
            return {
                "filename": filename,
                "markdown": f"处理 PDF 文件时出错: {str(e)}",
                "images": [],
                "duration": round(time.time() - start_time, 2)
            }

        # 文本提取过少时，启用 OCR 回退
        if enable_ocr and extracted_text_length < 100:
            logger.info(f"[PDF 回退] 提取文本过少({extracted_text_length}字符)，启用整页 OCR 回退处理")
            file_stream.seek(0)
            return self._fallback_pdf_to_images(
                file_stream, filename, image_mode, image_quality, max_image_size, enable_ocr, enable_llm
            )

        duration = time.time() - start_time

        return {
            "filename": filename,
            "markdown": markdown_text,
            "images": images,
            "duration": round(duration, 2)
        }

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
        """
        PDF OCR 回退处理 - 将每一页作为图片处理

        Args:
            file_stream: 文件二进制流
            filename: 文件名
            image_mode: 图片输出模式
            image_quality: 图片质量
            max_image_size: 图片最大尺寸
            enable_ocr: 是否启用 OCR
            enable_llm: 是否启用 LLM

        Returns:
            转换结果字典
        """
        start_time = time.time()
        images = []
        markdown_text = ""

        try:
            file_stream.seek(0)
            doc = fitz.open("pdf", file_stream.read())

            # 逐页渲染为图片
            for page_num in range(len(doc)):
                page = doc[page_num]

                # 渲染为 RGB 图片，200 DPI 以提高 OCR 准确度
                pix = page.get_pixmap(dpi=200, colorspace=fitz.csRGB)
                img_data = pix.tobytes("png")

                # OCR 回退处理时不压缩图片，保持原始质量
                processed_bytes = img_data
                img_ext = "png"

                img_name = f"page_{page_num + 1}.png"

                # 构建图片块
                image_block, image_info = image_processor.build_image_block(
                    img_name, processed_bytes, img_ext, image_mode, enable_ocr, enable_llm,
                    width=pix.width, height=pix.height
                )

                if image_info:
                    images.append(image_info)

                # 添加页标题
                image_block = f"## 第 {page_num + 1} 页\n\n" + image_block

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

    def _remove_duplicate_chars(self, text: str) -> str:
        """
        移除重复的连续字符

        Args:
            text: 原始文本

        Returns:
            清理后的文本
        """
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
        """
        格式化 PDF 文本行

        主要功能：
        1. 识别中文标题结构（编、章、节、条）
        2. 应用加粗格式

        Args:
            text: 原始文本
            bold_ranges: 加粗范围列表

        Returns:
            格式化后的文本
        """
        if not text or not text.strip():
            return text

        if bold_ranges is None:
            bold_ranges = []

        # 识别中文标题
        match = re.match(r'^(第[\s]*[一二三四五六七八九十百]+[\s]*编)(.*)$', text)
        if match:
            chapter = match.group(1).strip()
            content = match.group(2).strip()
            if content:
                return f"# {chapter}\n{content}"
            else:
                return f"# {chapter}"

        match = re.match(r'^(第[\s]*[一二三四五六七八九十百]+[\s]*章)(.*)$', text)
        if match:
            chapter = match.group(1).strip()
            content = match.group(2).strip()
            if content:
                return f"## {chapter}\n{content}"
            else:
                return f"## {chapter}"

        match = re.match(r'^(第[\s]*[一二三四五六七八九十百]+[\s]*节)(.*)$', text)
        if match:
            section = match.group(1).strip()
            content = match.group(2).strip()
            if content:
                return f"### {section}\n{content}"
            else:
                return f"### {section}"

        match = re.match(r'^(第[\s]*[一二三四五六七八九十百]+[\s]*条)(.*)$', text)
        if match:
            article = match.group(1).strip()
            content = match.group(2).strip()
            if content:
                return f"#### {article}\n{content}"
            else:
                return f"#### {article}"

        # 应用加粗格式
        if bold_ranges:
            result = []
            last_end = 0
            for start, end in bold_ranges:
                if start > last_end:
                    result.append(text[last_end:start])
                result.append(f"**{text[start:end]}**")
                last_end = end
            if last_end < len(text):
                result.append(text[last_end:])
            return ''.join(result)

        return text


# PDF 转换器单例实例
pdf_converter = PdfConverter()
