import time
from openai import OpenAI
from app.config import settings
from typing import Optional, BinaryIO
import base64
import json
from loguru import logger


class LLMService:
    def __init__(self):
        self._client = None
        self._initialized = False
    
    def _ensure_client(self):
        """延迟初始化 LLM 客户端，首次使用时才创建"""
        if self._initialized:
            return
        
        self._initialized = True
        
        if not settings.ENABLE_LLM:
            logger.debug("[LLMService] LLM 已禁用，跳过客户端初始化")
            return
        
        try:
            self._client = OpenAI(
                base_url=settings.LLM_BASE_URL,
                api_key=settings.LLM_API_KEY or "sk-local-model"
            )
            logger.info("[LLMService] LLM 客户端初始化完成: base_url={}, model={}", settings.LLM_BASE_URL, settings.LLM_MODEL)
        except Exception as e:
            logger.error("[LLMService] LLM 客户端初始化失败: {}", e)
            self._client = None

    @property
    def client(self):
        self._ensure_client()
        return self._client
    
    def _parse_extra_body(self) -> dict:
        """解析 extra_body 配置参数，从环境变量读取所有扩展参数"""
        result = {}
        
        if settings.LLM_EXTRA_BODY:
            try:
                result = json.loads(settings.LLM_EXTRA_BODY)
            except json.JSONDecodeError as e:
                logger.error("[LLMService] LLM_EXTRA_BODY 解析失败: {}", e)
        
        if settings.LLM_TOP_K is not None:
            result["top_k"] = settings.LLM_TOP_K
        
        if settings.LLM_REPETITION_PENALTY is not None:
            result["repetition_penalty"] = settings.LLM_REPETITION_PENALTY
        
        logger.debug("[LLMService] extra_body: {}", result)
        return result
    
    def _parse_extra_params(self) -> dict:
        """解析额外顶层参数，从环境变量读取"""
        result = {}
        
        if settings.LLM_EXTRA_PARAMS:
            try:
                result = json.loads(settings.LLM_EXTRA_PARAMS)
            except json.JSONDecodeError as e:
                logger.error("[LLMService] LLM_EXTRA_PARAMS 解析失败: {}", e)
        
        if result:
            logger.debug("[LLMService] extra_params: {}", result)
        return result
    
    def describe_image(
        self,
        image_stream: BinaryIO,
        content_type: str = "image/jpeg",
        prompt: Optional[str] = None
    ) -> str:
        _t0 = time.time()
        logger.info("[LLMService.describe_image] enter: content_type={}, prompt_len={}", content_type, len(prompt) if prompt else 0)
        if not self.client:
            logger.info("[LLMService.describe_image] exit: LLM disabled, duration={:.3f}s", time.time() - _t0)
            return ""
        
        try:
            image_bytes = image_stream.read()
            base64_image = base64.b64encode(image_bytes).decode("utf-8")
            data_uri = f"data:{content_type};base64,{base64_image}"
            
            # 阿里云Qwen API格式：不使用system role，将系统提示词放入user消息的开头
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": settings.LLM_SYSTEM_PROMPT + "\n\n" + (prompt or settings.LLM_PROMPT)},
                        {"type": "image_url", "image_url": {"url": data_uri, "detail": "auto"}}
                    ]
                }
            ]
            
            # 调试日志：分析提示词长度
            text_prompt = settings.LLM_SYSTEM_PROMPT + "\n\n" + (prompt or settings.LLM_PROMPT)
            text_char_count = len(text_prompt)
            image_byte_count = len(image_bytes)
            # 粗略估算：中文约 1.5-2 字符/Token，图片编码后约 1000 字节 ≈ 100-200 Token
            estimated_text_tokens = text_char_count // 2
            estimated_image_tokens = image_byte_count // 500
            
            logger.info("[LLMService] 请求配置: 模型={}, URL={}, 流式={}, Token统计={}", settings.LLM_MODEL, settings.LLM_BASE_URL, settings.LLM_STREAM, settings.LLM_INCLUDE_USAGE)
            logger.info("[LLMService] 提示词分析: 文本长度={} 字符, 估算 Token={}", text_char_count, estimated_text_tokens)
            logger.info("[LLMService] 图片分析: 图片大小={:.2f} KB, 估算 Token={}", image_byte_count/1024, estimated_image_tokens)
            logger.info("[LLMService] 估算总计: 约 {} Token", estimated_text_tokens + estimated_image_tokens)
            
            extra_body = self._parse_extra_body()
            extra_params = self._parse_extra_params()
            stream = settings.LLM_STREAM
            
            if stream:
                result = self._stream_request(messages, extra_body, extra_params)
            else:
                result = self._non_stream_request(messages, extra_body, extra_params)
            
            logger.info("[LLMService.describe_image] exit: result_len={}, duration={:.3f}s", len(result), time.time() - _t0)
            return result
        
        except Exception as e:
            logger.error("[LLMService.describe_image] exit: error={}, duration={:.3f}s", e, time.time() - _t0)
            return ""
    
    def _stream_request(self, messages: list, extra_body: dict, extra_params: dict = None) -> str:
        """流式请求"""
        stream_options = {}
        if settings.LLM_INCLUDE_USAGE:
            stream_options["include_usage"] = True
        
        logger.info("[LLMService] 流式应答开始接收")
        
        response = self.client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=messages,
            temperature=settings.LLM_TEMPERATURE,
            top_p=settings.LLM_TOP_P,
            max_tokens=settings.LLM_MAX_TOKENS,
            timeout=settings.LLM_TIMEOUT,
            stream=True,
            stream_options=stream_options,
            extra_body=extra_body,
            **extra_params if extra_params else {}
        )
        
        result_chunks = []
        reasoning_chunks = []
        chunk_count = 0
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        
        for chunk in response:
            # 获取 Token 用量信息（在最后一个 chunk 中）
            if chunk.usage:
                prompt_tokens = getattr(chunk.usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(chunk.usage, "completion_tokens", 0) or 0
                total_tokens = getattr(chunk.usage, "total_tokens", 0) or 0
                logger.info("[LLMService] Token 用量: prompt={}, completion={}, total={}", prompt_tokens, completion_tokens, total_tokens)
            
            if chunk.choices:
                delta = chunk.choices[0].delta
                
                # 打印 delta 中所有信息
                delta_info = []
                if hasattr(delta, "role") and delta.role:
                    delta_info.append(f"role={delta.role}")
                if hasattr(delta, "content") and delta.content is not None:
                    delta_info.append(f"content={delta.content}")
                
                # 尝试多种方式获取 reasoning_content（兼容不同网关）
                reasoning_content = None
                possible_fields = ["reasoning_content", "reasoning", "think", "thought", "thinking_content", "chain_of_thought"]
                
                # 方式 1: 直接属性访问
                for field in possible_fields:
                    if hasattr(delta, field):
                        val = getattr(delta, field)
                        if val is not None:
                            reasoning_content = val
                            break
                
                # 方式 2: 从 model_extra 获取（OpenAI SDK 新版）
                if reasoning_content is None and hasattr(delta, "model_extra"):
                    extra = delta.model_extra
                    if extra:
                        for field in possible_fields:
                            if field in extra and extra[field] is not None:
                                reasoning_content = extra[field]
                                break
                
                # 方式 3: 从 model_dump 获取
                if reasoning_content is None and hasattr(delta, "model_dump"):
                    dump = delta.model_dump()
                    if dump:
                        for field in possible_fields:
                            if field in dump and dump[field] is not None:
                                reasoning_content = dump[field]
                                break
                
                # 方式 4: 从 chunk 的原始数据获取（某些网关直接在 delta 里）
                if reasoning_content is None and hasattr(chunk, "model_dump"):
                    chunk_dump = chunk.model_dump()
                    if chunk_dump and "choices" in chunk_dump:
                        for choice in chunk_dump["choices"]:
                            if "delta" in choice:
                                delta_raw = choice["delta"]
                                for field in possible_fields:
                                    if field in delta_raw and delta_raw[field] is not None:
                                        reasoning_content = delta_raw[field]
                                        break
                
                if reasoning_content is not None:
                    delta_info.append(f"reasoning_content={reasoning_content}")
                    reasoning_chunks.append(reasoning_content)
                
                if hasattr(delta, "tool_calls") and delta.tool_calls:
                    delta_info.append(f"tool_calls={delta.tool_calls}")
                if hasattr(delta, "function_call") and delta.function_call:
                    delta_info.append(f"function_call={delta.function_call}")
                if hasattr(delta, "audio") and delta.audio:
                    delta_info.append(f"audio={delta.audio}")
                
                if delta_info:
                    chunk_count += 1
                    logger.debug("[LLMService] 流式 chunk #{}: {}", chunk_count, delta_info)
                
                # 收集 content 和 reasoning_content
                if delta.content:
                    result_chunks.append(delta.content)
        
        result = "".join(result_chunks)
        reasoning_result = "".join(reasoning_chunks)
        logger.info("[LLMService] 流式应答完成")
        logger.info("[LLMService] 流式应答: {} 个 chunk, content={} 字符, reasoning={} 字符", chunk_count, len(result), len(reasoning_result))
        
        # 验证 Token 用量与实际内容的一致性
        if completion_tokens > 0:
            chars_per_token = len(result) / completion_tokens if completion_tokens > 0 else 0
            logger.info("[LLMService] Token 统计验证: completion_tokens={}, 实际字符={}, 每Token平均字符={:.2f}", completion_tokens, len(result), chars_per_token)
            
            # 如果启用了思考模式，说明 Token 统计可能包含思考内容
            if reasoning_result:
                logger.info("[LLMService] 启用了思考模式，completion_tokens 可能包含思考内容的 Token 数")
        
        if reasoning_result:
            logger.debug("[LLMService] 推理内容预览: {}", reasoning_result[:500])
        return result
    
    def _non_stream_request(self, messages: list, extra_body: dict, extra_params: dict = None) -> str:
        """非流式请求"""
        logger.info("[LLMService] 非流式应答等待响应")
        
        response = self.client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=messages,
            temperature=settings.LLM_TEMPERATURE,
            top_p=settings.LLM_TOP_P,
            max_tokens=settings.LLM_MAX_TOKENS,
            timeout=settings.LLM_TIMEOUT,
            stream=False,
            extra_body=extra_body,
            **extra_params if extra_params else {}
        )
        
        result = response.choices[0].message.content if response.choices else ""
        
        # 记录 Token 用量信息
        if response.usage:
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens
            total_tokens = response.usage.total_tokens
            logger.info("[LLMService] Token 用量: prompt={}, completion={}, total={}", prompt_tokens, completion_tokens, total_tokens)
        
        logger.info("[LLMService] 非流式应答完成，内容长度: {} 字符", len(result))
        return result


llm_service = LLMService()