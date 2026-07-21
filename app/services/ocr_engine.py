"""
OCR 引擎进程 - 独立运行 PaddleOCR，通过 Unix Socket 对外提供服务

由 run_worker.py 启动为独立进程，每个进程只初始化一次 PaddleOCR，
所有 worker 进程通过 IPC 共享此引擎，避免重复初始化。
"""
import os
import gc
import time
import tempfile
from multiprocessing.connection import Listener
from loguru import logger

import paddle


def run_ocr_engine(socket_path: str):
    """OCR 引擎入口，由 run_worker.py 启动为独立进程"""
    logger.info("[OCREngine] 启动中, socket={}", socket_path)

    # 清理残留 socket 文件
    if os.path.exists(socket_path):
        os.unlink(socket_path)

    # 初始化 PaddleOCR
    from paddleocr import PaddleOCR

    os.environ['OMP_NUM_THREADS'] = '1'
    os.environ['MKL_NUM_THREADS'] = '1'
    os.environ['FLAGS_use_mkldnn'] = 'False'
    os.environ['FLAGS_use_onednn'] = 'False'
    os.environ['FLAGS_use_mkldnn_bf16'] = 'False'
    # 按需分配内存，避免 PaddlePaddle 预分配大内存池导致 OOM
    os.environ['FLAGS_allocator_strategy'] = 'auto_growth'
    # 限制 PaddlePaddle 内存池上限，防止多次推理后 OOM
    os.environ['FLAGS_fraction_of_cpu_memory_to_use'] = '0.3'
    # 启用 PaddlePaddle 垃圾回收策略，及时释放推理中间张量
    os.environ['FLAGS_eager_delete_tensor_gb'] = '0.0'

    try:
        if paddle.is_compiled_with_cuda():
            gpu_count = paddle.device.cuda.device_count()
            if gpu_count > 0:
                logger.info("[OCREngine] 检测到 {} 个 GPU", gpu_count)
    except Exception:
        pass

    ocr = PaddleOCR(
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False
    )
    logger.info("[OCREngine] PaddleOCR 初始化完成")

    # 主循环
    with Listener(socket_path, family='AF_UNIX') as listener:
        logger.info("[OCREngine] 开始监听: {}", socket_path)
        while True:
            conn = listener.accept()
            try:
                _handle_connection(conn, ocr)
            except Exception as e:
                logger.error("[OCREngine] 处理请求异常: {}", e)
            finally:
                try:
                    conn.close()
                except Exception:
                    pass


def _handle_connection(conn, ocr):
    """处理单个 OCR 请求"""
    image_data = conn.recv_bytes()
    _t0 = time.time()

    # 保存临时文件供 PaddleOCR 处理
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
        temp_path = f.name
        f.write(image_data)

    try:
        result = ocr.predict(temp_path)
    finally:
        try:
            os.unlink(temp_path)
        except Exception:
            pass

    # 解析结果
    text = _parse_paddleocr_result(result)
    duration = time.time() - _t0
    logger.debug(
        "[OCREngine] 处理完成: {} bytes -> {} chars, {:.3f}s",
        len(image_data), len(text) if text else 0, duration
    )

    result_bytes = text.encode('utf-8') if text else b''
    conn.send_bytes(result_bytes)

    # 释放显存/内存，防止多次请求后 OOM
    del result
    del text
    del image_data
    gc.collect()
    if paddle.is_compiled_with_cuda():
        paddle.device.cuda.empty_cache()
    # Linux 下将释放的内存归还给 OS（仅 glibc 有效）
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except Exception:
        pass


def _parse_paddleocr_result(result) -> str:
    """解析 PaddleOCR 3.7 结果，格式与 OCRService 兼容"""
    all_text = []
    try:
        for res_item in result:
            if hasattr(res_item, 'res'):
                res_data = res_item.res
            elif isinstance(res_item, dict):
                res_data = res_item
            else:
                continue

            ocr_result = None
            if isinstance(res_data, dict) and 'rec_texts' in res_data:
                ocr_result = res_data
            elif isinstance(res_data, dict) and 'res' in res_data:
                inner_res = res_data['res']
                if isinstance(inner_res, dict) and 'rec_texts' in inner_res:
                    ocr_result = inner_res

            if ocr_result:
                rec_texts = ocr_result.get('rec_texts', [])
                rec_scores = ocr_result.get('rec_scores', [])

                if isinstance(rec_texts, list) and isinstance(rec_scores, list):
                    for i, text in enumerate(rec_texts):
                        if isinstance(text, str) and len(text.strip()) > 0:
                            confidence = 0.0
                            if i < len(rec_scores):
                                try:
                                    confidence = float(rec_scores[i])
                                except (ValueError, TypeError):
                                    confidence = 0.0
                            all_text.append(f"{text.strip()}|{confidence:.2f}")

    except Exception as e:
        logger.error("[OCREngine] 解析结果失败: {}", e)

    return '\n'.join(all_text).strip() if all_text else ''