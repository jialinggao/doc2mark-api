"""
Structure 引擎进程 - 独立运行 PP-StructureV3，通过 Unix Socket 对外提供服务

职责：将支持的格式（PDF/图片等）通过 PP-StructureV3 转换为 Markdown，
      图片一律以 base64 内联在 markdown 中，由调用方决定如何使用。

由 run_worker.py 启动为独立进程，每个进程只初始化一次 PP-StructureV3，
所有 worker 进程通过 IPC 共享此引擎，避免重复初始化。
"""
import os
import io
import re
import gc
import time
import json
import base64
import tempfile
import shutil
from multiprocessing.connection import Listener
from loguru import logger
from PIL import Image


def run_structure_engine(socket_path: str):
    """Structure 引擎入口，由 run_worker.py 启动为独立进程"""
    logger.info("[StructureEngine] 启动中, socket={}", socket_path)

    # 清理残留 socket 文件
    if os.path.exists(socket_path):
        os.unlink(socket_path)

    # PaddlePaddle 内存管理：按需分配、限制池大小、及时回收
    os.environ['OMP_NUM_THREADS'] = '1'
    os.environ['MKL_NUM_THREADS'] = '1'
    os.environ['FLAGS_use_mkldnn'] = 'False'
    os.environ['FLAGS_use_onednn'] = 'False'
    os.environ['FLAGS_use_mkldnn_bf16'] = 'False'
    os.environ['FLAGS_allocator_strategy'] = 'auto_growth'
    os.environ['FLAGS_fraction_of_cpu_memory_to_use'] = '0.3'
    os.environ['FLAGS_eager_delete_tensor_gb'] = '0.0'

    # 初始化 PP-StructureV3
    from paddleocr import PPStructureV3

    pipeline = PPStructureV3(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=True,
        use_table_recognition=True,
        use_seal_recognition=True,
    )
    logger.info("[StructureEngine] PP-StructureV3 初始化完成")

    # 主循环
    with Listener(socket_path, family='AF_UNIX') as listener:
        logger.info("[StructureEngine] 开始监听: {}", socket_path)
        while True:
            conn = listener.accept()
            try:
                _handle_connection(conn, pipeline)
            except Exception as e:
                logger.error("[StructureEngine] 处理请求异常: {}", e)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass


def _handle_connection(conn, pipeline):
    """处理单个 Structure 请求"""
    start_time = time.time()

    # 接收请求元数据（仅需文件名）
    req = conn.recv()
    filename = req['filename']

    # 接收文件数据
    file_data = conn.recv_bytes()

    # 创建临时工作目录
    work_dir = tempfile.mkdtemp(prefix="structure_engine_")
    temp_file = os.path.join(work_dir, filename)

    try:
        with open(temp_file, 'wb') as f:
            f.write(file_data)

        logger.info(
            "[StructureEngine] 开始处理: {} ({} bytes)",
            filename, len(file_data)
        )

        # 运行 PP-StructureV3 管道
        results = pipeline.predict(input=temp_file)

        # 收集所有页面的 Markdown 和图片
        markdown_text = ""
        all_images = []

        if results is None:
            logger.warning("[StructureEngine] 返回空结果")
            conn.send({
                "markdown": "",
                "images": [],
                "duration": round(time.time() - start_time, 2)
            })
            return

        # 确保 results 是可迭代的
        if not isinstance(results, (list, tuple)):
            results = [results]

        for page_idx, res in enumerate(results):
            logger.debug("[StructureEngine] 处理第 {} 页", page_idx + 1)

            # 保存当前页面的 Markdown 和 JSON 到临时文件
            output_subdir = os.path.join(work_dir, f"page_{page_idx}")
            os.makedirs(output_subdir, exist_ok=True)
            res.save_to_markdown(output_subdir)
            res.save_to_json(output_subdir)

            # 读取生成的 Markdown 文件
            page_md = ""
            for md_file in sorted(os.listdir(output_subdir)):
                if not md_file.endswith('.md'):
                    continue
                md_path = os.path.join(output_subdir, md_file)
                with open(md_path, 'r', encoding='utf-8') as f:
                    page_md = f.read().strip()

            if not page_md:
                continue

            # 递归扫描所有子目录，收集图片并内联到 markdown
            image_extensions = ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')
            img_map = {}  # filename -> base64 数据 URI

            for root, dirs, files in os.walk(output_subdir):
                for img_file in sorted(files):
                    if not img_file.lower().endswith(image_extensions):
                        continue
                    img_path = os.path.join(root, img_file)
                    try:
                        with open(img_path, 'rb') as f:
                            img_data = f.read()

                        img = Image.open(io.BytesIO(img_data))
                        width, height = img.size

                        img_ext = img_file.rsplit('.', 1)[-1].lower()
                        img_b64 = base64.b64encode(img_data).decode('utf-8')
                        image_content = f"data:image/{img_ext};base64,{img_b64}"

                        all_images.append({
                            "name": f"page_{page_idx}_{img_file}",
                            "content": image_content,
                            "width": width,
                            "height": height
                        })

                        # 记录用于替换 markdown 中的相对路径
                        img_map[img_file] = image_content
                    except Exception as e:
                        logger.warning("[StructureEngine] 读取图片失败 {}: {}", img_file, e)

            # 将 markdown 中的 <img> 标签替换为标准 markdown 图片语法
            for filename, content in img_map.items():
                page_md = re.sub(
                    r'<img[^>]*' + re.escape(filename) + r'[^>]*>',
                    f'![{filename}]({content})',
                    page_md
                )

            # 清理多余的 <div> 包装
            page_md = re.sub(
                r'<div[^>]*>\s*(!\[.*?\]\(.*?\))\s*</div>',
                r'\1',
                page_md
            )
            # 清理 PP-StructureV3 生成的图片占位文本
            page_md = re.sub(r'\*\*\[图片\s*-\s*[^\]]*\]\*\*', '', page_md)

            # 追加到全局 markdown
            if markdown_text:
                markdown_text += "\n\n---\n\n"
            markdown_text += page_md

        duration = time.time() - start_time
        logger.info(
            "[StructureEngine] 处理完成: {} ({} 张图片, {:.2f}s)",
            filename, len(all_images), duration
        )

        # 如果 markdown 为空，尝试读取 JSON 结果作为备选
        if not markdown_text.strip():
            logger.warning("[StructureEngine] 未生成 Markdown，尝试从 JSON 提取文本")
            for page_idx, res in enumerate(results):
                json_dir = os.path.join(work_dir, f"page_{page_idx}")
                json_files = [f for f in os.listdir(json_dir) if f.endswith('.json')]
                for json_file in json_files:
                    json_path = os.path.join(json_dir, json_file)
                    try:
                        with open(json_path, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                        text_parts = _extract_text_from_json(data)
                        if text_parts:
                            if markdown_text:
                                markdown_text += "\n\n"
                            markdown_text += text_parts
                    except Exception:
                        pass

        conn.send({
            "markdown": markdown_text.strip(),
            "images": all_images,
            "duration": round(duration, 2)
        })

    except Exception as e:
        logger.error("[StructureEngine] 处理失败: {}", e)
        import traceback
        logger.error(traceback.format_exc())
        conn.send({
            "markdown": f"PP-StructureV3 处理失败: {str(e)}",
            "images": [],
            "duration": round(time.time() - start_time, 2)
        })
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

        # 释放内存，防止多次请求后 OOM
        try:
            del results
        except NameError:
            pass
        try:
            del all_images
        except NameError:
            pass
        try:
            del markdown_text
        except NameError:
            pass
        gc.collect()
        try:
            import ctypes
            ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception:
            pass


def _extract_text_from_json(data: dict) -> str:
    """从 PP-StructureV3 的 JSON 输出中提取文本"""
    texts = []

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