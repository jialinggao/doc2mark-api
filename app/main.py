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

app = FastAPI(
    title="Doc2MarkAPI",
    description="基于 FastAPI、Redis + RQ、MarkItDown、OpenAI SDK、Tesseract OCR 和 LibreOffice 构建的文档转 Markdown HTTP 服务。支持 PDF、Word、PPT、Excel、图片等格式统一转换为 Markdown，提供 OCR 图转文、多模态大模型图片描述、异步任务处理等功能。",
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
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.SERVICE_PORT,
        log_level=settings.LOG_LEVEL.lower()
    )
