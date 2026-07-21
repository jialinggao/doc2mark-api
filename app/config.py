from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import Optional


class Settings(BaseSettings):
    SERVICE_PORT: int = 5926
    LOG_LEVEL: str = "INFO"
    
    REDIS_HOST: str = "redis"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    
    # LLM 配置 - 支持本地模型
    LLM_BASE_URL: str = ""
    LLM_API_KEY: Optional[str] = None
    LLM_MODEL: str = ""
    LLM_PROMPT: str = ""
    LLM_SYSTEM_PROMPT: str = ""
    ENABLE_LLM: bool = False
    
    # LLM 参数配置（根据官网参数：最大输入 991K，最大输出 64K，上下文 1M，思维链 128K）
    LLM_TEMPERATURE: float = 0.7
    LLM_TOP_P: float = 0.9
    LLM_TOP_K: Optional[int] = None
    LLM_REPETITION_PENALTY: Optional[float] = None
    LLM_MAX_TOKENS: int = 16384
    LLM_TIMEOUT: int = 300
    LLM_STREAM: bool = True
    LLM_INCLUDE_USAGE: bool = True
    LLM_EXTRA_BODY: str = "{}"
    LLM_EXTRA_PARAMS: str = "{}"
    
    OCR_ENABLED: bool = True
    
    DEFAULT_IMAGE_QUALITY: int = 100
    DEFAULT_MAX_IMAGE_SIZE: int = -1

    USE_STRUCTURE_ENGINE: bool = False  # PDF 是否使用 PP-StructureV3 引擎转换
    
    MAX_REQUESTS_PER_MINUTE: int = 60
    ALLOWED_ORIGINS: str = "*"
    MAX_FILE_SIZE: int = 52428800
    
    TASK_TTL: int = 86400
    RESULT_TTL: int = 43200
    CLEANUP_INTERVAL_MINUTES: int = 10

    SYNC_TASK_TIMEOUT: int = 300
    ASYNC_TASK_TIMEOUT: int = 21600
    SYNC_WORKER_COUNT: int = 2
    ASYNC_WORKER_COUNT: int = 4
    
    model_config = {"env_file": ".env", "case_sensitive": True}
    
    @field_validator("LLM_TOP_K", "LLM_REPETITION_PENALTY", mode="before")
    @classmethod
    def empty_str_to_none(cls, v):
        """将空字符串转换为 None，支持 docker-compose 中空值环境变量"""
        if v == "" or v is None:
            return None
        return v


settings = Settings()
