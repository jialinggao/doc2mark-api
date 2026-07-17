from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from app.models import ConvertResponse, ImageMode, TaskResponse, TaskStatus, TaskResult, TaskResultData, ConvertData, ImageInfo, TaskData, HealthResponse
from app.config import settings
from uuid import uuid4
from rq import Queue
from redis import Redis
from datetime import datetime
from rq.job import Job
import asyncio
import json
import io
import time
from concurrent.futures import ThreadPoolExecutor
from app.utils.metrics import metrics_collector
from loguru import logger


router = APIRouter(prefix="/api", tags=["文档转换接口"])


start_time = datetime.now()

_wait_executor = ThreadPoolExecutor(max_workers=8)


def _blpop_wait(redis_conn, key, timeout):
    return redis_conn.blpop(key, timeout=timeout)


@router.post("/convert", response_model=ConvertResponse, summary="同步文档转换", description="上传文件并同步等待转换结果，适合小文件（建议 < 10MB）")
async def convert_document(
    file: UploadFile = File(..., description="要转换的文件（最大50MB）"),
    enable_ocr: bool = Form(False, description="是否启用OCR图转文"),
    enable_llm: bool = Form(False, description="是否启用多模态大模型图片描述"),
    image_mode: ImageMode = Form(ImageMode.BASE64, description="图片处理模式：base64/embed, placeholder/占位符, external/外部链接, none/不显示图片"),
    image_quality: int = Form(100, description="图片压缩质量（1-100，仅jpg有效）"),
    max_image_size: int = Form(-1, description="图片最大边长像素，-1表示不缩放")
):
    content = await file.read()
    if len(content) > settings.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大，最大支持 {settings.MAX_FILE_SIZE // 1024 // 1024}MB"
        )
    
    allowed_extensions = [
        "pdf", "ofd", "doc", "docx", "ppt", "pptx", "xls", "xlsx",
        "jpg", "jpeg", "png", "gif", "bmp", "tiff",
        "txt", "md", "html", "xml"
    ]
    file_ext = file.filename.split(".")[-1].lower() if file.filename else ""
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=415,
            detail=f"不支持的文件格式：{file_ext}"
        )

    task_id = str(uuid4())
    redis_conn = Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB
    )
    queue = Queue(name="sync_conversion", connection=redis_conn)

    job = queue.enqueue(
        'app.workers.tasks.process_document_task',
        task_id=task_id,
        file_content=content,
        filename=file.filename or "unknown",
        enable_ocr=enable_ocr,
        enable_llm=enable_llm,
        image_mode=image_mode.value,
        image_quality=image_quality,
        max_image_size=max_image_size,
        job_timeout=settings.SYNC_TASK_TIMEOUT + 60,
        result_ttl=300
    )

    result_key = f"task:result:{task_id}"
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(
        _wait_executor,
        _blpop_wait,
        redis_conn,
        result_key,
        settings.SYNC_TASK_TIMEOUT,
    )

    if raw is None:
        raise HTTPException(status_code=504, detail="转换超时，请尝试使用异步接口 /api/tasks")

    _, value = raw
    payload = json.loads(value)

    if payload.get("status") == "failed":
        raise HTTPException(status_code=500, detail=payload.get("error", "转换失败"))

    result = payload["result"]
    duration = result.get("duration", 0)
    convert_data = ConvertData(
        filename=result["filename"],
        markdown=result["markdown"],
        images=[ImageInfo(**img) for img in result.get("images", [])],
        duration=duration
    )
    
    task_type = metrics_collector._get_task_type(enable_ocr, enable_llm)
    metrics_collector.post_request_record(task_type, duration * 1000)

    return ConvertResponse(
        code=200,
        message="转换成功",
        data=convert_data
    )


@router.post("/tasks", response_model=TaskResponse, summary="提交异步转换任务", description="上传文件并异步提交转换任务，立即返回任务ID，适合大文件")
async def create_task(
    file: UploadFile = File(..., description="要转换的文件（最大50MB）"),
    enable_ocr: bool = Form(False, description="是否启用OCR图转文"),
    enable_llm: bool = Form(False, description="是否启用多模态大模型图片描述"),
    image_mode: ImageMode = Form(ImageMode.BASE64, description="图片处理模式：base64/embed, placeholder/占位符, external/外部链接, none/不显示图片"),
    image_quality: int = Form(100, description="图片压缩质量（1-100，仅jpg有效）"),
    max_image_size: int = Form(-1, description="图片最大边长像素，-1表示不缩放"),
    callback_url: str = Form(None, description="任务完成后的回调URL")
):
    content = await file.read()
    if len(content) > settings.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大，最大支持 {settings.MAX_FILE_SIZE // 1024 // 1024}MB"
        )
    
    task_id = str(uuid4())
    redis_conn = Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB
    )
    queue = Queue(name="async_conversion", connection=redis_conn)
    
    job = queue.enqueue(
        'app.workers.tasks.process_document_task',
        task_id=task_id,
        file_content=content,
        filename=file.filename or "unknown",
        enable_ocr=enable_ocr,
        enable_llm=enable_llm,
        image_mode=image_mode.value,
        image_quality=image_quality,
        max_image_size=max_image_size,
        callback_url=callback_url,
        job_timeout=settings.ASYNC_TASK_TIMEOUT,
        result_ttl=settings.RESULT_TTL,
        meta={"filename": file.filename or "unknown"}
    )
    
    # 使用 RQ job 的 ID 作为任务 ID
    task_id = job.id
    
    task_data = TaskData(
        task_id=task_id,
        status=TaskStatus.QUEUED,
        query_url=f"/api/tasks/{task_id}"
    )
    
    return TaskResponse(
        code=200,
        message="任务已提交",
        data=task_data
    )


@router.get("/tasks/{task_id}", response_model=TaskResult, summary="查询异步任务状态", description="根据任务ID查询转换进度和结果")
async def get_task_status(
    task_id: str
):
    redis_conn = Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB
    )
    
    try:
        job = Job.fetch(task_id, connection=redis_conn)
        
        if job.is_finished:
            result_data = job.result
            
            convert_data = None
            if result_data:
                convert_data = ConvertData(
                filename=result_data.get("filename", ""),
                markdown=result_data.get("markdown", ""),
                images=[ImageInfo(**img) for img in result_data.get("images", [])],
                duration=result_data.get("duration")
            )
            
            task_result_data = TaskResultData(
                task_id=task_id,
                status=TaskStatus.COMPLETED,
                progress=100,
                filename=result_data.get("filename") if result_data else None,
                result=convert_data
            )
            
            return TaskResult(
                code=200,
                data=task_result_data
            )
        
        elif job.is_failed:
            task_result_data = TaskResultData(
                task_id=task_id,
                status=TaskStatus.FAILED,
                progress=0,
                filename=job.meta.get("filename") if job.meta else None,
                error=str(job.exc_info)
            )
            
            return TaskResult(
                code=200,
                data=task_result_data
            )
        
        else:
            task_result_data = TaskResultData(
                task_id=task_id,
                status=TaskStatus.PROCESSING,
                progress=50
            )
            
            return TaskResult(
                code=200,
                data=task_result_data
            )
    
    except Exception as e:
        from app.utils.logging_config import log_error_with_context
        log_error_with_context(
            e,
            request_info={"method": "GET", "path": f"/api/tasks/{task_id}", "task_id": task_id},
        )
        raise HTTPException(status_code=404, detail=f"任务不存在：{task_id}")


@router.get("/health", response_model=HealthResponse, summary="健康检查", description="检查服务状态和各组件依赖情况")
async def health_check():
    redis_conn = Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB
    )
    
    redis_connected = False
    try:
        redis_conn.ping()
        redis_connected = True
    except:
        pass

    tesseract_available = False
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        tesseract_available = True
    except:
        pass

    llm_available = False
    try:
        from app.services.llm_service import llm_service
        llm_available = llm_service.client is not None
    except:
        pass

    uptime = int((datetime.now() - start_time).total_seconds())

    all_ok = redis_connected and tesseract_available and llm_available

    from app.models import HealthDependencies, HealthMetrics
    request_metrics = metrics_collector.get_metrics()

    return HealthResponse(
        status="ok" if all_ok else "degraded",
        service="Doc2MarkAPI 文档转换服务",
        version="1.0.0",
        uptime=uptime,
        dependencies=HealthDependencies(
            redis="healthy" if redis_connected else "unhealthy",
            tesseract="healthy" if tesseract_available else "unhealthy",
            llm="healthy" if llm_available else "unhealthy",
        ),
        metrics=HealthMetrics(
            total_requests=request_metrics["requests"]["total"],
            total_errors=metrics_collector.total_errors,
            avg_response_time_ms=request_metrics["performance"]["avg_response_time_ms"],
        ),
    )


@router.get("/formats", summary="查询支持的文件格式", description="返回支持的文件格式列表、图片处理模式、OCR和LLM配置")
async def get_supported_formats():
    return {
        "code": 200,
        "message": "success",
        "data": {
            "document": [
                "pdf",
                "ofd",
                "doc",
                "docx",
                "ppt",
                "pptx",
                "xls",
                "xlsx"
            ],
            "image": [
                "jpg",
                "jpeg",
                "png",
                "gif",
                "bmp",
                "tiff"
            ],
            "text": [
                "txt",
                "md",
                "html",
                "xml"
            ],
            "image_modes": [
                {
                    "mode": "base64",
                    "description": "将图片编码为 Base64 嵌入 Markdown",
                    "output_example": "![描述](data:image/png;base64,...)"
                },
                {
                    "mode": "placeholder",
                    "description": "将图片替换为占位符文本",
                    "output_example": "[图片：描述]"
                },
                {
                    "mode": "external",
                    "description": "Markdown 引用外部图片路径，图片文件单独返回",
                    "output_example": "![描述](images/image_001.png)"
                }
            ],
            "ocr": {
                "enabled": settings.OCR_ENABLED,
                "language": settings.OCR_LANGUAGE,
                "description": "OCR 图转文功能，提取图片中的文字内容"
            },
            "llm": {
                "enabled": settings.ENABLE_LLM,
                "model": settings.LLM_MODEL,
                "description": "多模态大模型图片描述功能"
            }
        }
    }


@router.get("/metrics", summary="获取监控指标", description="返回请求统计、性能指标、资源使用等监控数据")
async def get_metrics():
    redis_conn = Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB
    )
    
    sync_queue_pending = 0
    sync_queue_processing = 0
    async_queue_pending = 0
    async_queue_processing = 0
    
    try:
        from rq import Queue
        sync_queue = Queue(name="sync_conversion", connection=redis_conn)
        async_queue = Queue(name="async_conversion", connection=redis_conn)
        
        sync_queue_pending = len(sync_queue)
        sync_queue_processing = len(sync_queue.started_job_registry)
        
        async_queue_pending = len(async_queue)
        async_queue_processing = len(async_queue.started_job_registry)
    except:
        pass
    
    request_metrics = metrics_collector.get_metrics()
    resource_metrics = metrics_collector.get_resource_usage()
    
    alerts = []
    
    if request_metrics["requests"]["success_rate"] < 95:
        alerts.append({
            "level": "error",
            "message": f"错误率偏高 (成功率: {request_metrics['requests']['success_rate']}%)",
            "timestamp": datetime.now().isoformat()
        })
    
    if request_metrics["performance"]["p95_ms"] > 5000:
        alerts.append({
            "level": "warning",
            "message": f"响应时间偏高 (P95: {request_metrics['performance']['p95_ms']}ms)",
            "timestamp": datetime.now().isoformat()
        })
    
    if resource_metrics["cpu_usage"] > 80:
        alerts.append({
            "level": "warning",
            "message": f"CPU使用率偏高 ({resource_metrics['cpu_usage']}%)",
            "timestamp": datetime.now().isoformat()
        })
    
    if resource_metrics["memory_usage"] > 80:
        alerts.append({
            "level": "warning",
            "message": f"内存使用率偏高 ({resource_metrics['memory_usage']}%)",
            "timestamp": datetime.now().isoformat()
        })
    
    total_pending = sync_queue_pending + async_queue_pending
    if total_pending > 100:
        alerts.append({
            "level": "warning",
            "message": f"队列积压严重 (待处理: {total_pending})",
            "timestamp": datetime.now().isoformat()
        })
    
    return {
        "code": 200,
        "data": {
            "requests": request_metrics["requests"],
            "performance": request_metrics["performance"],
            "task_type_performance": request_metrics.get("task_type_performance", {}),
            "resources": resource_metrics,
            "queue": {
                "sync_queue": {
                    "pending_tasks": sync_queue_pending,
                    "processing_tasks": sync_queue_processing
                },
                "async_queue": {
                    "pending_tasks": async_queue_pending,
                    "processing_tasks": async_queue_processing
                },
                "total_pending": total_pending
            },
            "alerts": alerts
        }
    }
