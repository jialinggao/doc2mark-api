from app.services.converter import converter_service
from app.models import ImageMode
import io
import time
import json
import requests
from loguru import logger
from app.config import settings


def _notify_result(task_id, payload):
    try:
        from redis import Redis
        redis_conn = Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
        )
        result_key = f"task:result:{task_id}"
        redis_conn.lpush(result_key, json.dumps(payload, ensure_ascii=False, default=str))
        redis_conn.expire(result_key, 300)
    except Exception as e:
        logger.warning("[通知] 推送任务结果失败: {}", e)


def process_document_task(
    task_id: str,
    file_content: bytes,
    filename: str,
    enable_ocr: bool,
    enable_llm: bool,
    image_mode: str,
    image_quality: int = 100,
    max_image_size: int = -1,
    callback_url: str = None
):
    start_time = time.time()
    file_stream = io.BytesIO(file_content)
    
    try:
        result = converter_service.convert(
            file_stream=file_stream,
            filename=filename,
            enable_ocr=enable_ocr,
            enable_llm=enable_llm,
            image_mode=ImageMode(image_mode),
            image_quality=image_quality,
            max_image_size=max_image_size
        )
        
        duration = time.time() - start_time
        result["duration"] = round(duration, 2)
        
        if callback_url:
            try:
                requests.post(callback_url, json={
                    "task_id": task_id,
                    "status": "completed",
                    "result": result
                }, timeout=10)
                logger.info(f"回调通知发送成功: {callback_url}")
            except Exception as e:
                logger.error(f"回调通知失败: {callback_url}, 错误: {e}")
        
        _notify_result(task_id, {"status": "completed", "result": result})
        return result
    
    except Exception as e:
        if callback_url:
            try:
                requests.post(callback_url, json={
                    "task_id": task_id,
                    "status": "failed",
                    "error": str(e)
                }, timeout=10)
                logger.info(f"回调通知发送成功: {callback_url}")
            except Exception as callback_error:
                logger.error(f"回调通知失败: {callback_url}, 错误: {callback_error}")
        
        _notify_result(task_id, {"status": "failed", "error": str(e)})
        raise e
