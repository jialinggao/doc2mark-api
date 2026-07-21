import base64
import io
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import BinaryIO

from loguru import logger

from app.config import settings
from app.models import ImageMode
from app.services.image_processor import image_processor


class OfdConverter:
    """
    OFD 文档转换器
    
    转换策略：使用 Java OFD 转换器将 OFD 转换为 PDF，然后使用 PyMuPDF 处理
    依赖：tools/ofd-converter/target/ofd-converter-cli-1.0.0-jar-with-dependencies.jar
    
    OFD (Open Financial Document) 是中国自主研发的电子文档格式标准。
    """

    def __init__(self):
        # 容器环境固定路径（相对项目根目录）
        self.java_jar_path = os.path.join(
            os.path.dirname(__file__),
            "..", "..", "tools", "ofd-converter", "target", "ofd-converter-cli-1.0.0-jar-with-dependencies.jar"
        )
        # 转换为绝对路径
        self.java_jar_path = os.path.abspath(self.java_jar_path)
        
        if os.path.exists(self.java_jar_path):
            logger.info("[OFDConverter] JAR 文件路径: {}", self.java_jar_path)
        else:
            logger.error("[OFDConverter] JAR 文件未找到: {}", self.java_jar_path)

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
        转换 OFD 文档为 Markdown
        
        处理流程：OFD → (Java工具) → PDF → (PyMuPDF) → Markdown
        
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

        # 验证 JAR 文件是否存在
        if not os.path.exists(self.java_jar_path):
            return {
                "filename": filename,
                "markdown": f"OFD 转换器 JAR 文件未找到: {self.java_jar_path}",
                "images": [],
                "duration": round(time.time() - start_time, 2),
                "error": "JAR_FILE_NOT_FOUND"
            }

        temp_dir = None
        try:
            # 创建临时目录
            temp_dir = tempfile.mkdtemp(prefix="ofd_convert_")
            logger.debug("[OFDConverter] 创建临时目录: {}", temp_dir)

            # 保存 OFD 文件到临时目录
            ofd_file_path = os.path.join(temp_dir, "input.ofd")
            file_stream.seek(0)
            with open(ofd_file_path, "wb") as f:
                f.write(file_stream.read())

            # 转换 OFD 到 PDF
            pdf_file_path = os.path.join(temp_dir, "output.pdf")
            convert_success = self._convert_ofd_to_pdf(ofd_file_path, pdf_file_path)

            if not convert_success:
                return {
                    "filename": filename,
                    "markdown": "OFD 转换为 PDF 失败",
                    "images": [],
                    "duration": round(time.time() - start_time, 2),
                    "error": "OFD_TO_PDF_FAILED"
                }

            # 使用 PyMuPDF 处理 PDF
            import fitz
            doc = fitz.open(pdf_file_path)

            # 预检查：判断是否需要全文OCR（两种场景）
            # 1. 字体编码问题：Identity-H编码且缺少ToUnicode映射
            # 2. 文本提取过少问题：PDF可能全是图片或向量图形
            need_full_ocr = False
            full_ocr_reason = ""
            
            # 检查1：字体编码问题
            if self._need_ocr_due_to_font_encoding(doc):
                need_full_ocr = True
                full_ocr_reason = "字体编码问题(Identity-H缺少ToUnicode映射)"
            
            # 检查2：文本提取过少（先快速提取所有页面文本判断）
            if not need_full_ocr:
                total_text = ""
                for page_num in range(len(doc)):
                    total_text += doc[page_num].get_text("text")
                    if len(total_text) >= 100:
                        break  # 提前终止，已有足够文本
                
                if len(total_text) < 100:
                    need_full_ocr = True
                    full_ocr_reason = f"文本提取过少({len(total_text)}字符，可能是图片或向量图形PDF)"
            if need_full_ocr:
                logger.info("[OFDConverter] 检测到需要全文OCR: {}", full_ocr_reason)

            # 逐页处理 PDF
            for page_num in range(len(doc)):
                page = doc[page_num]

                page_text = ""
                page_text_count = 0

                # 如果需要全文OCR，直接渲染页面为图片并OCR
                if need_full_ocr:
                    try:
                        # 渲染页面为图片（指定RGB色彩空间）
                        pix = page.get_pixmap(dpi=200, colorspace=fitz.csRGB)
                        img_data = pix.tobytes("png")
                        
                        # 构建图片块并进行OCR（强制开启OCR）
                        img_name = f"page_{page_num + 1}.png"
                        image_block, image_info = image_processor.build_image_block(
                            img_name, img_data, "png", image_mode, True, enable_llm,
                            width=pix.width, height=pix.height
                        )
                        if image_info:
                            images.append(image_info)
                        page_text = f"## 第 {page_num + 1} 页\n\n" + image_block + "\n"
                        
                        # 页面分隔符
                        if page_num < len(doc) - 1:
                            page_text += "\n---\n\n"
                        
                        markdown_text += page_text
                    except Exception as e:
                        logger.error("[OFDConverter] 全文OCR处理第{}页失败: {}", page_num + 1, e)
                        page_text = f"## 第 {page_num + 1} 页\n\nOCR处理失败: {str(e)}\n"
                    
                    continue

                # 获取页面文本
                text_dict = page.get_text("dict")

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

            # 记录统计信息（在文档关闭前）
            page_count = len(doc)
            doc.close()

        except Exception as e:
            logger.error("[OFDConverter] 转换失败: {}", e)
            import traceback
            logger.error("[OFDConverter] 转换失败详情: {}", traceback.format_exc())
            return {
                "filename": filename,
                "markdown": f"处理 OFD 文件时出错: {str(e)}\n\n```\n{traceback.format_exc()}\n```",
                "images": [],
                "duration": round(time.time() - start_time, 2)
            }
        finally:
            # 清理临时目录
            if temp_dir and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                    logger.debug("[OFDConverter] 清理临时目录: {}", temp_dir)
                except Exception as e:
                    logger.warning("[OFDConverter] 清理临时目录失败: {}", e)

        duration = time.time() - start_time
        logger.info("[OFDConverter] 转换完成，共 {} 页，提取文本 {} 字符，耗时 {:.2f}s", page_count, extracted_text_length, duration)

        return {
            "filename": filename,
            "markdown": markdown_text,
            "images": images,
            "duration": round(duration, 2)
        }

    def _need_ocr_due_to_font_encoding(self, doc) -> bool:
        """
        检测PDF是否因为字体编码问题需要OCR识别
        
        检测逻辑（直接解析内嵌字体的CMAP表）：
        1. 检查是否使用内嵌字体
        2. 提取内嵌字体数据，直接解析其CMAP表
        3. 检查CMAP表的字符覆盖范围是否符合标准编码特征
        4. 如果是非标准CMAP + Identity-H编码 + 无ToUnicode映射 → 需要OCR
        
        判定标准：
        - 标准CMAP：字符覆盖范围符合GB2312/Unicode等标准编码
        - 非标准CMAP：字符范围异常（如从0x0001开始的自定义编码）
        
        Args:
            doc: PyMuPDF文档对象
        
        Returns:
            是否需要OCR识别
        """
        try:
            # CJK字体关键词（用于检测中文字体）
            cjk_keywords = [
                'GB2312', 'GB18030', 'CJK', 'SimSun', 'SimHei', 'KaiTi',
                'FZ', 'STSong', 'STHeiti', 'GB', 'Chinese', 'MSung', 'MHei'
            ]
            
            for page in doc:
                fonts = page.get_fonts(full=True)
                for font in fonts:
                    # 提取字体信息
                    font_num = font[0] if len(font) > 0 else 0        # 字体序号
                    is_embedded = font[1] if len(font) > 1 else 0   # 1=嵌入, 0=未嵌入
                    encoding = font[5] if len(font) > 5 else ''
                    font_name = font[3] if len(font) > 3 else ''
                    font_info = str(font[8]) if len(font) > 8 and font[8] else ''
                    
                    # 检查是否有ToUnicode映射
                    has_tounicode = 'ToUnicode' in font_info
                    
                    # 1. 判断是否是CJK字体（跳过英文字体）
                    is_cjk_font = any(keyword in font_name for keyword in cjk_keywords)
                    if not is_cjk_font:
                        continue
                    
                    # 2. 检查是否使用内嵌字体
                    if is_embedded == 0:
                        logger.debug("[OFDConverter] 字体未嵌入，使用系统字体: {}", font_name)
                        continue
                    
                    # 3. 检查编码方式
                    if encoding != 'Identity-H':
                        logger.debug("[OFDConverter] 使用标准字符集编码: {}, 字体: {}", encoding, font_name)
                        continue
                    
                    # 4. 检查是否有ToUnicode映射（有映射可以正确提取）
                    if has_tounicode:
                        logger.debug("[OFDConverter] 有 ToUnicode 映射，可以正确提取: {}", font_name)
                        continue
                    
                    # 5. 【关键改进】直接解析内嵌字体的CMAP表来判断是否标准
                    #    提取字体数据并解析CMAP表
                    font_data = doc.extract_font(font_num)
                    if font_data:
                        is_standard_cmap = self._is_standard_cmap(font_data)
                        if is_standard_cmap:
                            logger.debug("[OFDConverter] CMAP 表符合标准编码特征: {}", font_name)
                            continue
                        else:
                            logger.debug("[OFDConverter] CMAP 表不符合标准编码特征（可能是自定义编码）: {}", font_name)
                    else:
                        # 无法提取字体数据，保守起见判定为非标准CMAP
                        logger.debug("[OFDConverter] 无法提取字体数据，保守判定为非标准 CMAP: {}", font_name)
                    
                    # 6. 最终判断：内嵌字体 + Identity-H + 无ToUnicode + 非标准CMAP → 需要OCR
                    logger.debug("[OFDConverter] 需要 OCR - 内嵌字体: {}, 编码: {}, ToUnicode: {}, CMAP 标准: {}", font_name, encoding, has_tounicode, is_standard_cmap if font_data else '未知')
                    return True
            
            return False
        except Exception as e:
            logger.debug("[OFDConverter] 检测字体编码失败: {}", e)
            return False
    
    def _is_standard_cmap(self, font_data: bytes) -> bool:
        """
        简化判断：CMAP表是否标准
        
        判断规则：
        1. 必须有 Unicode Platform=0 子表
        2. 字形数量必须足够多（≥ 2000）
        两个条件都满足才判定为标准CMAP
        
        Args:
            font_data: 从PDF提取的字体二进制数据
        
        Returns:
            CMAP表是否符合标准编码特征
        """
        try:
            # 1. 检查是否是TrueType/OpenType字体
            if len(font_data) < 12 or font_data[:4] != b'\x00\x01\x00\x00':
                return True  # 非TTF字体，保守处理
            
            # 2. 解析TTF表目录，查找cmap和maxp表
            num_tables = int.from_bytes(font_data[4:6], 'big')
            table_dir_offset = 12
            
            cmap_offset = 0
            cmap_length = 0
            maxp_offset = 0
            
            for i in range(num_tables):
                entry_offset = table_dir_offset + i * 16
                if entry_offset + 16 > len(font_data):
                    break
                
                tag = font_data[entry_offset:entry_offset+4].decode('ascii', errors='ignore')
                if tag == 'cmap':
                    cmap_offset = int.from_bytes(font_data[entry_offset+8:entry_offset+12], 'big')
                    cmap_length = int.from_bytes(font_data[entry_offset+12:entry_offset+16], 'big')
                elif tag == 'maxp':
                    maxp_offset = int.from_bytes(font_data[entry_offset+8:entry_offset+12], 'big')
            
            # 3. 获取字形数量（从maxp表）
            num_glyphs = 0
            if maxp_offset + 6 <= len(font_data):
                num_glyphs = int.from_bytes(font_data[maxp_offset+4:maxp_offset+6], 'big')
            
            # 4. 检查是否有 Unicode Platform=0 子表
            has_unicode_platform0 = False
            if cmap_offset > 0 and cmap_offset + cmap_length <= len(font_data):
                cmap_data = font_data[cmap_offset:cmap_offset+cmap_length]
                if len(cmap_data) >= 4:
                    num_subtables = int.from_bytes(cmap_data[2:4], 'big')
                    for i in range(num_subtables):
                        subtable_entry_offset = 4 + i * 8
                        if subtable_entry_offset + 8 > len(cmap_data):
                            break
                        
                        platform_id = int.from_bytes(cmap_data[subtable_entry_offset:subtable_entry_offset+2], 'big')
                        if platform_id == 0:  # Unicode Platform
                            has_unicode_platform0 = True
                            break
            
            # 5. 综合判断：必须同时满足两个条件
            #    - 有 Unicode Platform=0 子表
            #    - 字形数量足够多（≥ 2000）
            is_standard = has_unicode_platform0 and num_glyphs >= 2000
            
            logger.debug("[OFDConverter] CMAP 标准性判断 - Unicode Platform=0: {}, 字形数量: {}, 标准: {}", has_unicode_platform0, num_glyphs, is_standard)
            
            return is_standard
        
        except Exception as e:
            logger.debug("[OFDConverter] 解析 CMAP 表失败: {}", e)
            return False  # 解析失败，保守判定为非标准

    def _convert_ofd_to_pdf(self, ofd_path: str, pdf_path: str) -> bool:
        """
        使用 Java 命令行工具将 OFD 转换为 PDF
        
        Args:
            ofd_path: OFD 文件路径
            pdf_path: 输出 PDF 文件路径
        
        Returns:
            是否转换成功
        """
        try:
            logger.info("[OFDConverter] 开始转换 OFD 到 PDF: {} -> {}", ofd_path, pdf_path)

            # 构建 Java 命令
            command = [
                "java", "-jar", self.java_jar_path,
                ofd_path, pdf_path, "pdf"
            ]

            logger.info("[OFDConverter] 执行命令: {}", ' '.join(command))

            # 执行命令
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=120  # 2分钟超时
            )

            if result.returncode == 0:
                if os.path.exists(pdf_path):
                    logger.info("[OFDConverter] OFD 转 PDF 成功")
                    return True
                else:
                    logger.error("[OFDConverter] 命令执行成功但 PDF 文件未生成")
                    return False
            else:
                logger.error("[OFDConverter] OFD 转 PDF 失败，返回码: {}", result.returncode)
                if result.stderr:
                    logger.error("[OFDConverter] 错误输出: {}", result.stderr[:2000])
                if result.stdout:
                    logger.debug("[OFDConverter] 标准输出: {}", result.stdout[:2000])
                return False

        except subprocess.TimeoutExpired:
            logger.error("[OFDConverter] OFD 转 PDF 超时")
            return False
        except Exception as e:
            logger.error("[OFDConverter] OFD 转 PDF 异常: {}", e)
            return False

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


# OFD 转换器单例实例
ofd_converter = OfdConverter()
