import base64
import io
import re
from typing import BinaryIO, Tuple, List, Dict
from PIL import Image
from loguru import logger
from app.models import ImageMode
from app.config import settings
from app.services.ocr_service import ocr_service
from app.services.llm_service import llm_service


class ImageProcessor:
    """图片处理工具类，负责图片压缩、格式化和 OCR/LLM 描述"""

    def compress_image(self, image_data: bytes, quality: int = 100, max_size: int = -1) -> Tuple[bytes, str]:
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
            return image_data, 'png'

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
    ) -> Tuple[str, Dict]:
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
                image_block += f"**[OCR 识别 - {img_name}]**\n\n{clean_text}\n\n"
            else:
                image_block += f"**[OCR 识别 - {img_name}]**\n\n（未识别出有效文字）\n\n"
        
        if enable_llm and settings.ENABLE_LLM:
            llm_desc = llm_service.describe_image(io.BytesIO(processed_bytes), f"image/{img_ext}")
            if llm_desc:
                image_block += f"**[LLM 描述 - {img_name}]**\n\n{llm_desc}\n\n"
            else:
                image_block += f"**[LLM 描述 - {img_name}]**\n\n（描述生成失败）\n\n"
        
        image_block += self.format_image_by_mode(img_name, image_mode, image_info)
        
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
        image_pattern = r'!\[([^\]]*)\]\(data:image/(\w+);base64,([A-Za-z0-9+/=]+)\)'
        image_matches = re.findall(image_pattern, markdown_text)
        
        images = []
        result_text = markdown_text
        
        for idx, (alt_text, img_type, base64_data) in enumerate(image_matches):
            img_name = f"image_{idx + 1}.{img_type}"
            img_data = base64.b64decode(base64_data)
            
            if max_image_size > 0 or image_quality < 100:
                processed_bytes, img_ext = self.compress_image(img_data, image_quality, max_image_size)
            else:
                processed_bytes = img_data
                img_ext = img_type.lower()
            
            image_block, image_info = self.build_image_block(
                img_name, processed_bytes, img_ext, image_mode, enable_ocr, enable_llm
            )
            
            images.append(image_info)
            
            original_base64_uri = f"![{alt_text}](data:image/{img_type};base64,{base64_data})"
            result_text = result_text.replace(original_base64_uri, image_block, 1)
        
        return result_text, images

    def insert_image_blocks(self, markdown_text: str, image_blocks: list) -> str:
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


image_processor = ImageProcessor()
