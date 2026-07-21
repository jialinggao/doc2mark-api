import os
import sys
import json
import traceback
from datetime import datetime
from loguru import logger


LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "logs")
LOG_DIR = os.path.normpath(LOG_DIR)


def _ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)


def _json_format(record):
    try:
        record["extra"]["serialized"] = json.dumps(
            {
                "timestamp": record["time"].isoformat(),
                "level": record["level"].name,
                "message": record["message"],
                "module": record["module"],
                "function": record["function"],
                "line": record["line"],
                "extra": record["extra"],
            },
            ensure_ascii=False,
            default=str,
        )
    except Exception:
        record["extra"]["serialized"] = json.dumps(
            {
                "timestamp": datetime.now().isoformat(),
                "level": record["level"].name,
                "message": record["message"],
            },
            ensure_ascii=False,
        )
    return "{extra[serialized]}\n"


def _error_json_format(record):
    try:
        data = {
            "timestamp": record["time"].isoformat(),
            "level": record["level"].name,
            "message": record["message"],
            "module": record["module"],
            "function": record["function"],
            "line": record["line"],
            "extra": {k: v for k, v in record["extra"].items() if k != "serialized"},
        }
        if record["exception"]:
            data["exception"] = {
                "type": record["exception"].type.__name__ if record["exception"].type else None,
                "value": str(record["exception"].value) if record["exception"].value else None,
                "traceback": record["exception"].traceback if record["exception"].traceback else None,
            }
        record["extra"]["serialized"] = json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        record["extra"]["serialized"] = json.dumps(
            {
                "timestamp": datetime.now().isoformat(),
                "level": record["level"].name,
                "message": record["message"],
            },
            ensure_ascii=False,
        )
    return "{extra[serialized]}\n"


def setup_logging(log_level: str = "INFO"):
    _ensure_log_dir()

    logger.remove()

    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{module}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level=log_level,
        colorize=True,
    )

    logger.add(
        os.path.join(LOG_DIR, "app.log"),
        format=_json_format,
        level=log_level,
        rotation="10 MB",
        retention="30 days",
        compression="gz",
        encoding="utf-8",
        enqueue=True,
    )

    logger.add(
        os.path.join(LOG_DIR, "error.log"),
        format=_error_json_format,
        level="ERROR",
        rotation="10 MB",
        retention="30 days",
        compression="gz",
        encoding="utf-8",
        enqueue=True,
        backtrace=True,
        diagnose=True,
    )

    logger.info("[LogConfig] 日志系统初始化完成，日志目录: {}", LOG_DIR)
    return logger


def log_access(
    method: str,
    path: str,
    status_code: int,
    duration_ms: float,
    client_ip: str = "-",
    user_agent: str = "-",
    request_id: str = None,
    error_detail: str = None,
):
    _ensure_log_dir()
    access_data = {
        "timestamp": datetime.now().isoformat(),
        "type": "access",
        "method": method,
        "path": path,
        "status_code": status_code,
        "duration_ms": round(duration_ms, 2),
        "client_ip": client_ip,
        "user_agent": user_agent,
    }
    if request_id:
        access_data["request_id"] = request_id
    if error_detail:
        access_data["error_detail"] = error_detail

    access_file = os.path.join(LOG_DIR, "access.log")
    line = json.dumps(access_data, ensure_ascii=False, default=str) + "\n"
    try:
        with open(access_file, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        logger.warning("[LogConfig] 写入访问日志失败: {}/{}", method, path)


def log_error_with_context(
    error: Exception,
    request_info: dict = None,
    extra: dict = None,
):
    context = {
        "timestamp": datetime.now().isoformat(),
        "type": "error_detail",
        "exception_type": type(error).__name__,
        "exception_message": str(error),
        "traceback": traceback.format_exc(),
    }
    if request_info:
        context["request"] = request_info
    if extra:
        context["extra"] = extra

    logger.error(
        "[LogConfig] {}: {}",
        type(error).__name__,
        str(error),
        extra={"error_context": context},
    )
