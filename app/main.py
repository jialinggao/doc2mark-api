from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.api.routes import router
from app.api.middleware import RateLimitMiddleware, MetricsCollectorMiddleware, AccessLogMiddleware, setup_cors
from app.config import settings
from app.utils.logging_config import setup_logging
from app.utils.cleanup import start_cleanup_task
import uvicorn


logger = setup_logging(log_level=settings.LOG_LEVEL)

BANNER = r"""

▄▄▄▄▄▄               ▄▄▄▄▄▄▄  ▄▄▄      ▄▄▄                      ▄▄▄▄   ▄▄▄▄▄▄▄   ▄▄▄▄▄ 
███▀▀██▄             ▀▀▀▀████ ████▄  ▄████             ▄▄     ▄██▀▀██▄ ███▀▀███▄  ███  
███  ███ ▄███▄ ▄████    ▄██▀  ███▀████▀███  ▀▀█▄ ████▄ ██ ▄█▀ ███  ███ ███▄▄███▀  ███  
███  ███ ██ ██ ██     ▄███▄▄▄ ███  ▀▀  ███ ▄█▀██ ██ ▀▀ ████   ███▀▀███ ███▀▀▀▀    ███  
██████▀  ▀███▀ ▀████ ████████ ███      ███ ▀█▄██ ██    ██ ▀█▄ ███  ███ ███       ▄███▄ 

---------------------------------------------------------------------------------------
                               
                                     ▗ 
                               ▝▀▖▛▀▖▄ 
                               ▞▀▌▙▄▘▐ 
                               ▝▀▘▌  ▀▘

  api 服务主要功能:
    - 提供文档转 Markdown 的 REST API 接口，支持同步/异步请求
    - 接收文件上传、校验、入队，通过 Redis 转发给 Worker 处理
    - 支持同步等待（小文件）和异步轮询（大文件）两种模式
    - 内置请求限流、CORS、访问日志、指标采集等中间件
    - 提供监控面板 (/monitor) 和健康检查接口 (/api/health)
"""

app = FastAPI(
    title="Doc2MarkAPI",
    description="基于 FastAPI、Redis + RQ、MarkItDown、OpenAI SDK、PaddleOCR 和 LibreOffice 构建的文档转 Markdown HTTP 服务。支持 PDF、Word、PPT、Excel、图片等格式统一转换为 Markdown，提供 OCR 图转文、多模态大模型图片描述、异步任务处理等功能。",
    version="1.0.0"
)

start_cleanup_task(app)
setup_cors(app)
app.add_middleware(AccessLogMiddleware)
app.add_middleware(MetricsCollectorMiddleware)
app.add_middleware(RateLimitMiddleware)

app.include_router(router)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/monitor", summary="监控面板")
async def monitor_panel():
    return FileResponse("app/static/monitor.html")


@app.get("/", tags=["基础信息"])
async def root():
    return {
        "service": "Doc2MarkAPI",
        "version": "1.0.0",
        "docs": "/docs"
    }


if __name__ == "__main__":
    logger.info("\n{}", BANNER)
    logger.info("[Main] Doc2MarkAPI v{} 服务启动中...", "1.0.0")
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.SERVICE_PORT,
        log_level=settings.LOG_LEVEL.lower()
    )
