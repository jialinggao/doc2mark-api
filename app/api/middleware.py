from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from collections import defaultdict
from datetime import datetime, timedelta
from app.config import settings
from fastapi.middleware.cors import CORSMiddleware
import time
from loguru import logger


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = (time.time() - start_time) * 1000
            from app.utils.logging_config import log_access
            log_access(
                method=request.method,
                path=str(request.url.path),
                status_code=500,
                duration_ms=duration_ms,
                client_ip=request.client.host if request.client else "-",
                user_agent=request.headers.get("user-agent", "-"),
                error_detail=str(exc),
            )
            raise

        duration_ms = (time.time() - start_time) * 1000
        from app.utils.logging_config import log_access
        error_detail = None
        if response.status_code >= 400:
            error_detail = f"HTTP {response.status_code}"
        log_access(
            method=request.method,
            path=str(request.url.path),
            status_code=response.status_code,
            duration_ms=duration_ms,
            client_ip=request.client.host if request.client else "-",
            user_agent=request.headers.get("user-agent", "-"),
            error_detail=error_detail,
        )
        return response


class MetricsCollectorMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        
        response = await call_next(request)
        
        duration_ms = (time.time() - start_time) * 1000
        
        from app.api.metrics import metrics_collector
        is_error = response.status_code >= 400
        metrics_collector.record_request(duration_ms, is_error)
        
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.request_history = defaultdict(list)
    
    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host
        now = datetime.now()
        
        cutoff = now - timedelta(minutes=1)
        self.request_history[client_ip] = [
            ts for ts in self.request_history[client_ip]
            if ts > cutoff
        ]
        
        if len(self.request_history[client_ip]) >= settings.MAX_REQUESTS_PER_MINUTE:
            return JSONResponse(
                status_code=429,
                content={"detail": "请求过于频繁，请稍后再试"}
            )
        
        self.request_history[client_ip].append(now)
        
        response = await call_next(request)
        return response


def setup_cors(app):
    origins = settings.ALLOWED_ORIGINS.split(",")
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
