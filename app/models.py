from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class ImageMode(str, Enum):
    BASE64 = "base64"
    PLACEHOLDER = "placeholder"
    EXTERNAL = "external"
    NONE = "none"


class TaskStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ImageInfo(BaseModel):
    name: str = Field(..., description="图片文件名")
    content: str = Field(..., description="图片内容（Base64编码或路径）")
    width: int = Field(..., description="图片宽度（像素）")
    height: int = Field(..., description="图片高度（像素）")


class ConvertData(BaseModel):
    filename: str = Field(..., description="原文件名")
    markdown: str = Field(..., description="转换后的 Markdown 内容")
    images: List[ImageInfo] = Field(default=[], description="提取的图片信息列表")
    duration: Optional[float] = Field(None, description="转换耗时（秒）")


class ConvertResponse(BaseModel):
    code: int = Field(default=200, description="状态码")
    message: str = Field(default="转换成功", description="响应消息")
    data: ConvertData = Field(..., description="转换结果数据")


class TaskData(BaseModel):
    task_id: str = Field(..., description="任务唯一标识")
    status: TaskStatus = Field(..., description="任务状态")
    query_url: str = Field(..., description="查询任务状态的接口地址")


class TaskResponse(BaseModel):
    code: int = Field(default=200, description="状态码")
    message: str = Field(default="任务已提交", description="响应消息")
    data: TaskData = Field(..., description="任务数据")


class TaskResultData(BaseModel):
    task_id: str = Field(..., description="任务唯一标识")
    status: TaskStatus = Field(..., description="任务状态：queued/processing/completed/failed")
    progress: int = Field(default=0, description="进度百分比（0-100）")
    filename: Optional[str] = Field(None, description="原文件名")
    result: Optional[ConvertData] = Field(None, description="转换结果数据（完成时返回）")
    error: Optional[str] = Field(None, description="错误信息（失败时返回）")


class TaskResult(BaseModel):
    code: int = Field(default=200, description="状态码")
    data: TaskResultData = Field(..., description="任务结果数据")


class HealthDependencies(BaseModel):
    redis: str = Field(..., description="Redis 连接状态：healthy/unhealthy")
    ocr_engine: str = Field(..., description="OCR 引擎可用状态：healthy/unhealthy")
    llm: str = Field(..., description="LLM 大模型服务可用状态：healthy/unhealthy")


class HealthMetrics(BaseModel):
    total_requests: int = Field(..., description="总请求数")
    total_errors: int = Field(..., description="总错误数")
    avg_response_time_ms: float = Field(..., description="平均响应时间（毫秒）")


class HealthResponse(BaseModel):
    status: str = Field(..., description="服务状态：ok/degraded")
    service: str = Field(default="Doc2MarkAPI 文档转换服务", description="服务名称")
    version: str = Field(..., description="服务版本")
    uptime: int = Field(..., description="运行时间（秒）")
    dependencies: Optional[HealthDependencies] = Field(None, description="各组件依赖状态")
    metrics: Optional[HealthMetrics] = Field(None, description="基础请求指标")
