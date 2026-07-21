import base64
import io
import os
import re
import subprocess
import tempfile
import time
from typing import BinaryIO, Tuple, List, Dict, Optional
from PIL import Image
import fitz  # PyMuPDF
from loguru import logger
from app.models import ImageMode
from app.config import settings
from app.services.ocr_service import ocr_service
from app.services.llm_service import llm_service


class ImageProcessor:
    """图片处理工具类，负责图片压缩、格式化和 OCR/LLM 描述"""

    # 非标准图片格式映射（无法用 PIL 处理，需用 LibreOffice 转换）
    _UNSUPPORTED_IMAGE_FORMATS = {'x-wmf', 'x-emf', 'wmf', 'emf'}

    def _convert_unsupported_format(self, image_data: bytes, img_type: str) -> Tuple[bytes, str]:
        """用 LibreOffice 将 WMF/EMF 等非标准格式转为 PNG"""
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                src_ext = img_type.replace('x-', '')
                src_path = os.path.join(tmpdir, f"input.{src_ext}")
                with open(src_path, 'wb') as f:
                    f.write(image_data)

                # 先转为 PDF（LibreOffice 单独渲染 WMF 会生成全透明 PNG）
                result = subprocess.run(
                    ['libreoffice', '--headless', '--convert-to', 'pdf', '--outdir', tmpdir, src_path],
                    capture_output=True, text=True, timeout=60
                )

                if result.returncode != 0:
                    logger.warning(f"LibreOffice 转换 {img_type} 为 PDF 失败: {result.stderr[:200]}")
                    return image_data, 'png'

                pdf_path = os.path.join(tmpdir, 'input.pdf')
                if not os.path.exists(pdf_path):
                    logger.warning(f"PDF 文件未生成: {pdf_path}")
                    return image_data, 'png'

                # 用 PyMuPDF 渲染 PDF 页面为图像
                doc = fitz.open(pdf_path)
                page = doc[0]
                pix = page.get_pixmap(dpi=200)
                pil_img = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
                doc.close()

                # 裁剪非白色内容区域（去除 LibreOffice 添加的空白边距）
                gray = pil_img.convert('L')
                pixels_arr = list(gray.getdata())
                w, h = pil_img.size
                threshold = 250

                non_white_rows = [y for y in range(h) if any(p < threshold for p in pixels_arr[y*w:(y+1)*w])]
                non_white_cols = [x for x in range(w) if any(pixels_arr[y*w + x] < threshold for y in range(h))]

                if non_white_rows and non_white_cols:
                    y1, y2 = min(non_white_rows), max(non_white_rows)
                    x1, x2 = min(non_white_cols), max(non_white_cols)
                    pil_img = pil_img.crop((x1, y1, x2 + 1, y2 + 1))

                buf = io.BytesIO()
                pil_img.save(buf, format='PNG')
                return buf.getvalue(), 'png'

        except FileNotFoundError:
            logger.warning("LibreOffice 不可用，无法转换非标准图片格式")
        except Exception as e:
            logger.warning(f"转换 {img_type} 图片时出错: {e}")

        # 转换失败时返回原始数据，但标记为 png 以保持兼容
        return image_data, 'png'

    def compress_image(self, image_data: bytes, quality: int = 100, max_size: int = -1, original_ext: str = '') -> Tuple[bytes, str]:
        # 非 PIL 支持的格式，直接返回原始数据
        if original_ext.lower() in self._UNSUPPORTED_IMAGE_FORMATS:
            return image_data, original_ext.lower()

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
                img.save(output_buffer, format=img_format, compress_level=9)
            else:
                img.save(output_buffer, format=img_format)
            
            return output_buffer.getvalue(), img_format.lower()
        
        except Exception as e:
            logger.error(f"压缩图片失败: {e}")
            return image_data, original_ext or 'png'

    def format_image_by_mode(
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
        
        elif image_mode == ImageMode.NONE:
            return ""
        
        return ""

    def build_image_block(
        self,
        img_name: str,
        processed_bytes: bytes,
        img_ext: str,
        image_mode: ImageMode,
        enable_ocr: bool = False,
        enable_llm: bool = False,
        width: int = 0,
        height: int = 0
    ) -> Tuple[str, Optional[Dict]]:
        _t0 = time.time()
        logger.debug("[ImageProcessor.build_image_block] enter: img_name={}, image_mode={}, enable_ocr={}, enable_llm={}, size={}x{}",
                      img_name, image_mode, enable_ocr, enable_llm, width, height)
        image_block = ""

        # NONE 模式：只做 OCR/LLM，不生成图片信息
        if image_mode == ImageMode.NONE:
            if enable_ocr and settings.OCR_ENABLED:
                ocr_text = ocr_service.extract_text_from_image(processed_bytes)
                if ocr_text:
                    clean_text = ocr_service.clean_ocr_text(ocr_text)
                    image_block += f"{clean_text}\n\n"

            if enable_llm and settings.ENABLE_LLM:
                llm_desc = llm_service.describe_image(io.BytesIO(processed_bytes), f"image/{img_ext}")
                if llm_desc:
                    image_block += f"{llm_desc}\n\n"

            return image_block, None

        image_info = {
            "name": img_name,
            "content": f"data:image/{img_ext};base64,{base64.b64encode(processed_bytes).decode()}",
            "width": width,
            "height": height
        }

        if width == 0 and height == 0:
            try:
                img = Image.open(io.BytesIO(processed_bytes))
                image_info["width"] = img.width
                image_info["height"] = img.height
            except:
                pass
        
        image_block = ""
        
        if enable_ocr and settings.OCR_ENABLED:
            ocr_text = ocr_service.extract_text_from_image(processed_bytes)
            if ocr_text:
                clean_text = ocr_service.clean_ocr_text(ocr_text)
                image_block += f"{clean_text}\n\n"
        
        if enable_llm and settings.ENABLE_LLM:
            llm_desc = llm_service.describe_image(io.BytesIO(processed_bytes), f"image/{img_ext}")
            if llm_desc:
                image_block += f"{llm_desc}\n\n"
        
        image_block += self.format_image_by_mode(img_name, image_mode, image_info)

        logger.debug("[ImageProcessor.build_image_block] exit: img_name={}, block_len={}, has_image={}, duration={:.3f}s",
                      img_name, len(image_block), image_info is not None, time.time() - _t0)
        return image_block, image_info

    def process_markdown_images(
        self,
        markdown_text: str,
        image_mode: ImageMode,
        image_quality: int,
        max_image_size: int,
        enable_ocr: bool,
        enable_llm: bool
    ) -> Tuple[str, List[Dict]]:
        _t0 = time.time()
        image_pattern = r'!\[([^\]]*)\]\(data:image/([\w.+-]+);base64,([A-Za-z0-9+/=]+)\)'
        image_matches = re.findall(image_pattern, markdown_text)

        logger.info("[ImageProcessor.process_markdown_images] enter: image_count={}, image_mode={}, enable_ocr={}, enable_llm={}",
                     len(image_matches), image_mode, enable_ocr, enable_llm)
        
        images = []
        result_text = markdown_text
        
        for idx, (alt_text, img_type, base64_data) in enumerate(image_matches):
            original_img_type = img_type  # 保存原始类型，用于后续替换
            img_name = f"image_{idx + 1}.{img_type}"
            img_data = base64.b64decode(base64_data)
            
            # 对 WMF/EMF 等非标准格式，用 LibreOffice 转为 PNG
            if img_type.lower() in self._UNSUPPORTED_IMAGE_FORMATS:
                img_data, img_type = self._convert_unsupported_format(img_data, img_type.lower())
                img_name = f"image_{idx + 1}.png"
            
            if max_image_size > 0 or image_quality < 100:
                processed_bytes, img_ext = self.compress_image(img_data, image_quality, max_image_size, img_type.lower())
            else:
                processed_bytes = img_data
                img_ext = img_type.lower()
            
            image_block, image_info = self.build_image_block(
                img_name, processed_bytes, img_ext, image_mode, enable_ocr, enable_llm
            )
            
            if image_info:
                images.append(image_info)
            
            original_base64_uri = f"![{alt_text}](data:image/{original_img_type};base64,{base64_data})"
            result_text = result_text.replace(original_base64_uri, image_block, 1)
        
        logger.info("[ImageProcessor.process_markdown_images] exit: processed={}/{} images, result_len={}, duration={:.3f}s",
                     len(images), len(image_matches), len(result_text), time.time() - _t0)
        return result_text, images

    def insert_image_blocks(self, markdown_text: str, image_blocks: list) -> str:
        _t0 = time.time()
        logger.info("[ImageProcessor.insert_image_blocks] enter: paragraph_count={}, image_count={}, text_len={}",
                     len(re.split(r'\n\n+', markdown_text.strip())) if markdown_text.strip() else 0,
                     len(image_blocks), len(markdown_text))
        paragraphs = re.split(r'\n\n+', markdown_text.strip())
        
        if len(paragraphs) <= 1:
            if markdown_text:
                result = markdown_text + "\n\n---\n\n" + "\n\n---\n\n".join(image_blocks)
                logger.info("[ImageProcessor.insert_image_blocks] exit: result_len={}, duration={:.3f}s",
                             len(result), time.time() - _t0)
                return result
            else:
                result = "\n\n---\n\n".join(image_blocks)
                logger.info("[ImageProcessor.insert_image_blocks] exit: result_len={}, duration={:.3f}s",
                             len(result), time.time() - _t0)
                return result
        
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
        
        result = "\n\n".join(result)
        logger.info("[ImageProcessor.insert_image_blocks] exit: result_len={}, duration={:.3f}s",
                     len(result), time.time() - _t0)
        return result


image_processor = ImageProcessor()
