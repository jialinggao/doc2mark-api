# Doc2MarkAPI

基于 FastAPI、Redis + RQ、MarkItDown、OpenAI SDK、PaddleOCR 3.7（PP-OCRv6）和 LibreOffice 构建的文档转 Markdown HTTP 服务。将 PDF、Word、PPT、Excel、图片等多种格式统一转换为 Markdown，支持旧版 .doc/.ppt/.xls 格式自动转换、OCR 图转文（支持 GPU 加速）、多模态大模型图片描述、异步任务处理，提供 RESTful API 接口，支持 Docker 容器化部署。

## 功能特性

| 功能 | 说明 |
|------|------|
| 文档转换 | 支持 PDF、Word(.doc/.docx)、PPT(.ppt/.pptx)、Excel(.xls/.xlsx)、图片等 |
| 旧版格式兼容 | .doc/.ppt/.xls 自动转换为新版格式（LibreOffice） |
| 智能 .doc 处理 | 自动识别并优化 HTML 包装的 .doc 格式 |
| OCR 图转文 | PaddleOCR 3.7（PP-OCRv6），中英文识别，支持 GPU 加速 |
| 多模态图片描述 | 兼容 OpenAI SDK 的多模态模型 |
| 同步/异步模式 | 小文件同步转换，大文件异步任务 |
| 图片处理 | Base64 嵌入 / 占位符 / 外部链接 / 无图片四种模式 |
| PDF 智能回退 | 扫描件 PDF 自动渲染为图片 |
| 限流保护 | 防止 API 滥用 |
| 监控面板 | 内置实时监控面板，无需额外部署 |
| 分离架构 | API 轻量容器 + Worker 完整容器（支持 CPU/GPU 版本） |
| 引擎进程隔离 | OCR（PaddleOCR）和结构分析（PP-StructureV3）独立进程运行，通过 Unix Socket 与 Worker 通信 |
| 资源预加载 | 重量级资源在 Worker 进程 fork 前统一初始化，子进程通过写时复制共享，避免重复加载 |
| 引擎自动恢复 | 监控线程实时检测引擎进程状态，异常退出自动重启 |

## 架构说明

### API 与 Worker 分工

- **API 容器**：轻量容器，仅接收请求、校验、提交任务到 Redis 队列、返回响应，不执行转换计算
- **Worker 容器**：完整容器，执行文档转换（MarkItDown/OCR/LLM/图片处理），支持 CPU/GPU 两个版本

### 引擎进程架构

Worker 容器内运行三个独立进程：

```
run_worker.py（主进程）
  ├── OCR Engine Process（PaddleOCR）
  │   └── Unix Socket: /tmp/ocr.sock
  ├── Structure Engine Process（PP-StructureV3）
  │   └── Unix Socket: /tmp/structure.sock
  ├── Engine Monitor Thread（崩溃自动重启）
  └── Worker Process（监听 sync_conversion + async_conversion 双队列）
```

- **OCR 引擎**：独立进程运行 PaddleOCR，所有 Worker 通过 Unix Socket 共享同一实例，避免 GPU 资源重复初始化
- **Structure 引擎**：独立进程运行 PP-StructureV3，提供 PDF 版面分析、表格识别、印章识别能力
- **引擎监控**：守护线程每 10 秒检查引擎进程状态，异常退出时自动重启，保障服务稳定性

### 队列架构

| 队列 | 用途 | Worker 进程数 |
|------|------|--------------|
| `sync_conversion` | 同步接口提交的任务（需快速响应） | SYNC_WORKER_COUNT（默认 1） |
| `async_conversion` | 异步接口提交的任务（可容忍延迟） | 合并到同一 Worker 进程 |

Worker 进程同时监听 `sync_conversion` 和 `async_conversion` 两个队列，确保同步任务不会被异步任务阻塞。

### 资源预加载

Worker 主进程在 fork 子进程前调用 `_preload_worker_resources()` 初始化以下资源：
- 所有转换器模块（PDF、Word、PPT、Excel、图片、OFD）
- MarkItDown 实例
- LLM 客户端
- 各模块依赖的图片处理器、OCRFallback 等

子进程通过写时复制（Copy-on-Write）继承预加载的资源，避免每个任务重复初始化。

### 同步接口流程

```
客户端 → API 提交到 sync_conversion 队列 → BLPOP 阻塞等待结果 → 返回响应
                                              ↓
                 Worker 取任务 → 通过 Unix Socket 调用引擎 → 执行转换 → LPUSH 结果通知
```

## 快速开始

### Docker 部署（推荐）

#### CPU 版本
```bash
cd docker
cp .env.example .env
# 编辑 .env 配置 LLM 等参数
docker-compose up -d --build
```

#### GPU 版本
1. 确保宿主机已配置 NVIDIA 驱动和 nvidia-docker
2. 编辑 `docker-compose.yml`，将 worker 的 dockerfile 改为 `docker/Dockerfile.worker.gpu`
3. 取消 worker 的 `deploy.resources` GPU 配置注释
4. 启动服务
```bash
cd docker
cp .env.example .env
docker-compose up -d --build
```

服务启动后访问：
- API 文档：http://localhost:5926/docs
- 健康检查：http://localhost:5926/api/health
- 监控面板：http://localhost:5926/monitor


## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/convert` | POST | 同步文档转换（提交队列等待结果） |
| `/api/tasks` | POST | 提交异步转换任务 |
| `/api/tasks/{task_id}` | GET | 查询任务状态 |
| `/api/health` | GET | 健康检查（含组件依赖状态和指标） |
| `/api/metrics` | GET | 获取监控指标 |
| `/api/formats` | GET | 查询支持的格式 |
| `/monitor` | GET | 监控面板（HTML） |

### 同步转换示例

```bash
curl -X POST http://localhost:5926/api/convert \
  -F "file=@document.pdf" \
  -F "enable_ocr=true" \
  -F "enable_llm=true" \
  -F "image_mode=base64"
```

### 异步转换示例

```bash
# 提交任务
curl -X POST http://localhost:5926/api/tasks \
  -F "file=@large-document.pdf" \
  -F "callback_url=http://your-server.com/webhook"

# 查询结果
curl http://localhost:5926/api/tasks/{task_id}
```

### 监控指标查询

```bash
curl http://localhost:5926/api/metrics
```

响应示例：
```json
{
  "code": 200,
  "data": {
    "requests": {
      "total": 12345,
      "today": 156,
      "success_rate": 99.2
    },
    "performance": {
      "avg_response_time_ms": 1250,
      "p50_ms": 800,
      "p95_ms": 3500,
      "p99_ms": 8000
    },
    "resources": {
      "cpu_usage": 45,
      "memory_usage": 62,
      "disk_usage": 38
    },
    "queue": {
      "pending_tasks": 5,
      "processing_tasks": 2
    },
    "alerts": []
  }
}
```

## 监控面板

服务内置一个简易监控页面，无需额外部署任何软件：

**访问地址**：http://localhost:5926/monitor

**监控面板特性**：

| 功能 | 说明 |
|------|------|
| 实时状态 | 服务状态、运行时长、版本信息 |
| 请求统计 | 总请求数、今日请求、成功率 |
| 性能指标 | P50/P95/P99 响应时间 |
| 资源使用 | CPU、内存、磁盘使用率 |
| 队列状态 | 待处理/处理中任务数 |
| 告警展示 | 最近告警列表 |
| 自动刷新 | 每 30 秒自动更新 |

**使用方式**：
1. 在内网浏览器访问 `http://localhost:5926/monitor`
2. 直接查看实时监控数据
3. 无需额外安装任何软件

**告警机制**：
- 错误率 > 5% 触发错误告警
- P95 响应时间 > 5000ms 触发警告
- CPU/内存使用率 > 80% 触发警告
- 队列积压 > 100 触发警告

## 配置说明

在 `docker/.env` 中配置（完整参数见 `.env.example`）：

### 基础配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `SERVICE_PORT` | 服务端口 | 5926 |
| `LOG_LEVEL` | 日志级别 | INFO |
| `REDIS_HOST` | Redis 地址 | redis |

### LLM 配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `ENABLE_LLM` | 启用大模型 | false |
| `LLM_BASE_URL` | 大模型 API 地址 | - |
| `LLM_API_KEY` | 大模型 API 密钥 | - |
| `LLM_MODEL` | 模型名称 | - |

### OCR 配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `OCR_ENABLED` | 启用 OCR | true |

### 服务限制

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `MAX_REQUESTS_PER_MINUTE` | 限流阈值 | 60 |
| `MAX_FILE_SIZE` | 文件大小限制 | 52428800 (50MB) |

### Worker 配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `SYNC_TASK_TIMEOUT` | 同步任务等待超时（秒） | 300 |
| `ASYNC_TASK_TIMEOUT` | 异步任务执行超时（秒） | 21600 |
| `SYNC_WORKER_COUNT` | Worker 进程数 | 1 |

## 日志系统

| 日志文件 | 内容 | 说明 |
|---------|------|------|
| `logs/app.log` | 全量应用日志（INFO 及以上） | JSON 格式，10MB 轮转，保留 30 天 |
| `logs/error.log` | 错误日志（ERROR 及以上） | JSON 格式，含完整异常堆栈 |
| `logs/access.log` | 访问日志 | 每个请求的方法、路径、状态码、耗时 |

## 图片处理模式

通过 `image_mode` 参数控制：

| 模式 | 输出 | 适用场景 |
|------|------|----------|
| `base64` | `![描述](data:image/png;base64,...)` | 本地笔记、单文件分发 |
| `placeholder` | `[图片：描述]` | 知识库、语义检索 |
| `external` | `![描述](images/image_001.png)` | Web 预览 |
| `none` | 完全隐藏，不输出任何图片标记 | 纯文本场景 |

## 项目结构

```
├── app/                    # 应用代码
│   ├── api/                # API 路由和中间件
│   │   ├── routes.py       # API 接口定义
│   │   ├── middleware.py   # 中间件（限流、访问日志、CORS）
│   │   └── metrics.py     # 指标收集
│   ├── services/           # 核心服务
│   │   ├── converter.py         # 统一转换入口（路由分发）
│   │   ├── pdf_converter.py     # PDF 转换（含 OCR 回退）
│   │   ├── pdf_structure_converter.py # PP-StructureV3 客户端代理
│   │   ├── word_converter.py    # Word 转换
│   │   ├── ppt_converter.py     # PPT 转换
│   │   ├── excel_converter.py   # Excel 转换
│   │   ├── image_converter.py   # 图片转换
│   │   ├── ofd_converter.py     # OFD 转换
│   │   ├── general_converter.py # 通用转换器
│   │   ├── ocr_engine.py        # OCR 引擎进程（PaddleOCR）
│   │   ├── structure_engine.py  # Structure 引擎进程（PP-StructureV3）
│   │   ├── ocr_service.py       # OCR 客户端代理
│   │   ├── ocr_fallback.py      # OCR 回退处理
│   │   ├── llm_service.py       # LLM 客户端
│   │   └── image_processor.py   # 图片处理
│   ├── workers/            # 异步任务
│   │   └── tasks.py        # 任务执行 + 结果通知
│   ├── utils/              # 工具模块
│   │   ├── logging_config.py # 日志配置
│   │   └── cleanup.py       # 定时清理
│   ├── main.py             # API 入口
│   ├── config.py           # 配置
│   ├── models.py           # 数据模型
│   └── run_worker.py       # Worker 入口（引擎进程 + Worker 进程）
├── docker/                 # Docker 配置
│   ├── Dockerfile.api      # API 轻量镜像
│   ├── Dockerfile.worker   # Worker CPU 镜像
│   ├── Dockerfile.worker.gpu # Worker GPU 镜像
│   ├── docker-compose.yml  # 编排配置
│   ├── .env                # 环境变量
│   ├── .env.example        # 环境变量模板
│   └── volumes/            # 数据挂载目录
├── docs/                   # 设计文档
├── tests/                  # 测试用例
├── requirements.txt        # Worker Python 依赖
├── requirements.api.txt    # API Python 依赖
└── .dockerignore           # Docker 忽略文件
```

## 技术栈

| 组件 | 版本 |
|------|------|
| FastAPI | >=0.136.1 |
| Redis + RQ | Redis 8.6.2 / RQ >=2.8.0 |
| MarkItDown | >=0.1.5 |
| OpenAI SDK | >=2.36.0 |
| PaddleOCR | 3.7.0 |
| PaddlePaddle | 3.1.0 |
| PyMuPDF | >=1.23.0 |
| Python | 3.11 |

## License

MIT