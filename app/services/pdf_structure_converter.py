"""
PDF 文档转换器 - 使用 PP-StructureV3

使用 PaddleOCR PP-StructureV3 进行版面分析、表格识别、公式识别和图表理解，
将 PDF 转换为结构化 Markdown 文档。

注意：此模块为实验性功能，通过 converter.py 中的 USE_PDF_STRUCTURE_V3 配置切换。
"""
import os
import io
import time
import json
import tempfile
import shutil
from typing import BinaryIO
from loguru import logger
from PIL import Image
from app.models import ImageMode
from app.config import settings


class PdfStructureConverter:
    """
    PDF 结构转换器 - 基于 PP-StructureV3

    主要功能：
    1. 使用 PP-StructureV3 进行版面分析（文本、表格、图片、公式等）
    2. 自动恢复阅读顺序，处理多栏排版
    3. 表格识别并转换为 Markdown 格式
    4. 公式识别（LaTeX 输出）
    5. 输出结构化 Markdown 文档

    与 pdf_converter.py 的区别：
    - pdf_converter.py：基于 PyMuPDF 文本提取 + 规则解析
    - pdf_structure_converter.py：基于 PP-StructureV3 视觉解析（AI 驱动）
    """

    def __init__(self):
        self._pipeline = None
        self._initialized = False

    def _initialize(self):
        """延迟初始化 PP-StructureV3 管道"""
        if self._initialized:
            return

        logger.info("首次使用 PP-StructureV3，初始化文档解析管道...")
        try:
            from paddleocr import PPStructureV3

            self._pipeline = PPStructureV3(
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=True,
                use_table_recognition=True,
                use_seal_recognition=True,
            )
            logger.info("PP-StructureV3 初始化成功")
            self._initialized = True
        except Exception as e:
            logger.error(f"PP-StructureV3 初始化失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise

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
            enable_ocr: 是否启用 OCR（PP-StructureV3 内置 OCR，此参数不直接影响）
            enable_llm: 是否启用 LLM（暂不支持）
            image_mode: 图片输出模式
            image_quality: 图片质量（暂不支持）
            max_image_size: 图片最大尺寸（暂不支持）

        Returns:
            标准转换结果字典
        """
        start_time = time.time()

        # 延迟初始化
        try:
            self._initialize()
        except Exception:
            logger.error("PP-StructureV3 不可用，转换失败")
            return {
                "filename": filename,
                "markdown": f"PP-StructureV3 初始化失败，请检查 PaddleOCR 安装。",
                "images": [],
                "duration": round(time.time() - start_time, 2)
            }

        # 创建临时目录保存输入 PDF 和输出结果
        work_dir = tempfile.mkdtemp(prefix="pdf_structure_")
        temp_pdf = os.path.join(work_dir, filename)

        try:
            # 将文件流保存到临时文件
            file_stream.seek(0)
            with open(temp_pdf, 'wb') as f:
                f.write(file_stream.read())

            logger.info(f"[PP-StructureV3] 开始处理: {filename}")

            # 运行 PP-StructureV3 管道
            results = self._pipeline.predict(input=temp_pdf)

            # 收集所有页面的 Markdown 和图片
            markdown_text = ""
            all_images = []

            if results is None:
                logger.warning("[PP-StructureV3] 返回空结果")
                return {
                    "filename": filename,
                    "markdown": "",
                    "images": [],
                    "duration": round(time.time() - start_time, 2)
                }

            # 确保 results 是可迭代的
            if not isinstance(results, (list, tuple)):
                results = [results]

            for page_idx, res in enumerate(results):
                logger.debug(f"[PP-StructureV3] 处理第 {page_idx + 1} 页")

                # 保存当前页面的 Markdown 到临时文件
                output_subdir = os.path.join(work_dir, f"page_{page_idx}")
                os.makedirs(output_subdir, exist_ok=True)
                res.save_to_markdown(output_subdir)
                res.save_to_json(output_subdir)

                # 读取生成的 Markdown 文件
                md_files = [f for f in os.listdir(output_subdir) if f.endswith('.md')]
                for md_file in md_files:
                    md_path = os.path.join(output_subdir, md_file)
                    with open(md_path, 'r', encoding='utf-8') as f:
                        page_md = f.read()
                        if page_md.strip():
                            if markdown_text:
                                markdown_text += "\n\n---\n\n"
                            markdown_text += page_md

                # 处理页面中的图片资源
                # PP-StructureV3 会在输出目录下生成图片文件
                if image_mode != ImageMode.NONE:
                    image_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')
                    for img_file in sorted(os.listdir(output_subdir)):
                        if img_file.lower().endswith(image_extensions):
                            img_path = os.path.join(output_subdir, img_file)
                            try:
                                with open(img_path, 'rb') as f:
                                    img_data = f.read()

                                img = Image.open(io.BytesIO(img_data))
                                width, height = img.size

                                if image_mode == ImageMode.BASE64:
                                    import base64
                                    img_b64 = base64.b64encode(img_data).decode('utf-8')
                                    image_content = f"data:image/{img_file.rsplit('.', 1)[-1].lower()};base64,{img_b64}"
                                else:
                                    image_content = img_file

                                all_images.append({
                                    "name": f"page_{page_idx}_{img_file}",
                                    "content": image_content,
                                    "width": width,
                                    "height": height
                                })
                            except Exception as e:
                                logger.warning(f"[PP-StructureV3] 读取图片失败 {img_file}: {e}")

            duration = time.time() - start_time
            logger.info(f"[PP-StructureV3] 处理完成: {filename} ({len(all_images)} 张图片, {duration:.2f}s)")

            # 如果 markdown 为空，尝试读取 JSON 结果作为备选
            if not markdown_text.strip():
                logger.warning("[PP-StructureV3] 未生成 Markdown 内容，尝试从 JSON 提取文本")
                for page_idx, res in enumerate(results):
                    json_dir = os.path.join(work_dir, f"page_{page_idx}")
                    json_files = [f for f in os.listdir(json_dir) if f.endswith('.json')]
                    for json_file in json_files:
                        json_path = os.path.join(json_dir, json_file)
                        try:
                            with open(json_path, 'r', encoding='utf-8') as f:
                                data = json.load(f)
                            # 从 JSON 中提取文本
                            text_parts = self._extract_text_from_json(data)
                            if text_parts:
                                if markdown_text:
                                    markdown_text += "\n\n"
                                markdown_text += text_parts
                        except Exception:
                            pass

            return {
                "filename": filename,
                "markdown": markdown_text.strip(),
                "images": all_images,
                "duration": round(duration, 2)
            }

        except Exception as e:
            logger.error(f"[PP-StructureV3] 处理失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return {
                "filename": filename,
                "markdown": f"PP-StructureV3 处理失败: {str(e)}",
                "images": [],
                "duration": round(time.time() - start_time, 2)
            }
        finally:
            # 清理临时目录
            shutil.rmtree(work_dir, ignore_errors=True)

    def _extract_text_from_json(self, data: dict) -> str:
        """从 PP-StructureV3 的 JSON 输出中提取文本"""
        texts = []

        # 递归搜索文本字段
        def _search(obj, depth=0):
            if depth > 10:
                return
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key in ('text', 'content', 'description', 'rec_text') and isinstance(value, str) and value.strip():
                        texts.append(value.strip())
                    else:
                        _search(value, depth + 1)
            elif isinstance(obj, list):
                for item in obj:
                    _search(item, depth + 1)

        _search(data)
        return '\n'.join(texts) if texts else ""


# 单例实例
pdf_structure_converter = PdfStructureConverter()