# Doc2MarkAPI 文档转换服务设计文档

**文档版本**：V1.0\
**创建日期**：2026-05-15\
**创建人**：AI 助手\
**文档状态**：初稿

***

## 修订记录

| 版本   | 日期         | 修订内容                    | 修订人   |
| ---- | ---------- | ----------------------- | ----- |
| V1.0 | 2026-05-15 | 初始版本，包含架构设计、接口规范、图片处理策略 | AI 助手 |
| V1.1 | 2026-05-19 | 删除 API 版本管理功能；删除 pages 字段；更新错误码规范；更新依赖版本；补充增强功能说明 | AI 助手 |
| V1.2 | 2026-05-19 | 新增旧版 Office 格式支持（doc/ppt/xls），通过 LibreOffice 自动转换 | AI 助手 |
| V1.3 | 2026-05-19 | 项目名称更新为 Doc2MarkAPI | AI 助手 |

***

## 目录

1. [项目概述](#1-项目概述)
2. [系统架构](#2-系统架构)
3. [接口设计规范](#3-接口设计规范)
4. [图片处理策略](#4-图片处理策略)
5. [异步任务处理](#5-异步任务处理)
6. [性能优化](#6-性能优化)
7. [部署方案](#7-部署方案)
8. [安全与监控](#8-安全与监控)
9. [附录](#9-附录)

***

## 1. 项目概述

### 1.1 项目背景

在知识库构建、文档数字化、内容管理系统等场景中，需要将多种格式的文档（PDF、Word、PPT、Excel、图片等）统一转换为 Markdown 格式，以便于后续的文本处理、语义检索和大模型应用。MarkItDown 是微软开源的文档转换工具，支持丰富的输入格式和 OCR 功能，但缺乏标准化的 HTTP 接口和容器化部署方案。

### 1.2 项目目标

- 构建一个基于 MarkItDown 的 HTTP 服务，提供标准化的文档转换接口
- 支持同步和异步两种调用模式，适应不同场景的需求
- 支持 OCR 图转文和多模态大模型图片描述
- 提供灵活的图片处理策略，适配 Dify 等知识库平台
- 支持 Docker 容器化部署，便于集成到现有系统

### 1.3 核心功能

| 功能模块    | 功能描述                                    | 优先级 |
| ------- | --------------------------------------- | --- |
| 文档转换    | 支持 PDF、Word(.doc/.docx)、PPT(.ppt/.pptx)、Excel(.xls/.xlsx)、图片等格式 | P0  |
| 旧版格式兼容 | .doc/.ppt/.xls 自动转换为新版格式（LibreOffice）    | P1  |
| OCR 图转文 | 提取图片中的文字内容                              | P0  |
| 多模态图片描述 | 调用大模型生成图片的语义描述                          | P1  |
| 异步任务处理  | 支持大文件、多图片文档的异步转换                        | P0  |
| 图片处理    | 支持多种图片处理模式（Base64/占位符/描述/外部链接）          | P1  |
| 健康检查    | 提供服务状态监控接口                              | P0  |
| 限流保护    | 防止 API 滥用，支持每分钟请求数限制                    | P1  |
| PDF 智能回退 | 当 MarkItDown 无法提取内容时自动渲染为图片              | P1  |

***

## 2. 系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                        客户端系统                           │
│  (Dify 知识库 / 内容管理系统 / 其他业务系统)                │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                   负载均衡器 (Nginx)                        │
│              proxy_read_timeout: 600s                       │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                   API 服务层 (FastAPI)                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │ 同步转换接口│  │ 异步转换接口│  │ 任务查询接口│          │
│  └─────────────┘  └─────────────┘  └─────────────┘          │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                   任务队列 (Redis + RQ)                     │
│  ┌────────────────────────────┐  ┌──────────────────────┐   │
│  │  Queue: sync_conversion    │  │  Queue: async_conversion │   │
│  │  (同步任务，BLPOP 等待结果)│  │  (异步任务，RQ 标准消费) │   │
│  └────────────────────────────┘  └──────────────────────┘   │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                   Worker 工作节点                           │
│  ┌─────────────────────┐  ┌─────────────────────┐          │
│  │ Sync Worker (×2)    │  │ Async Worker (×4)   │          │
│  │ 消费 sync_conversion│  │ 消费 async_conversion│          │
│  │ (OCR+LLM)           │  │ (OCR+LLM)           │          │
│  └─────────────────────┘  └─────────────────────┘          │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                   MarkItDown 转换引擎                       │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │ 文档解析器  │  │ OCR 引擎    │  │ LLM 客户端  │          │
│  │ (Tesseract) │  │ (Tesseract) │  │ (OpenAI)    │          │
│  └─────────────┘  └─────────────┘  └─────────────┘          │
└─────────────────────────────────────────────────────────────┘

```

### 2.2 技术选型

| 组件     | 技术方案                    | 版本                 | 说明               |
| ------ | ----------------------- | ------------------ | ---------------- |
| API 框架 | FastAPI                 | >=0.136.1          | 高性能异步 Web 框架     |
| 任务队列   | Redis + RQ              | Redis 7 / RQ >=2.8.0 | 轻量级任务队列          |
| 文档转换   | MarkItDown              | >=0.1.5            | 微软开源文档转换工具       |
| 格式转换   | LibreOffice             | 最新版                | 旧版 Office 转新版    |
| OCR 引擎 | Tesseract + pytesseract | 5.x                | 支持中文简体/繁体        |
| 大模型客户端 | OpenAI SDK              | >=2.36.0           | 支持 GPT-4o 等多模态模型 |
| PDF 处理  | PyMuPDF (fitz)          | 最新版                | PDF 渲染和图片提取     |
| 容器化    | Docker + Docker Compose | 最新版                | 标准化部署            |
| 反向代理   | Nginx                   | 1.24+              | 负载均衡和超时控制        |

### 2.3 核心流程

**同步转换流程**：

1. 客户端发送 POST 请求，上传文件
2. API 服务校验文件格式和大小
3. 将任务提交到 `sync_conversion` 队列
4. API 通过 Redis BLPOP 阻塞等待 Worker 返回结果
5. 返回转换后的 Markdown 内容（超时返回 504）

**异步转换流程**：

1. 客户端发送 POST 请求，上传文件
2. API 服务校验文件，生成任务 ID
3. 将任务信息存入 Redis，加入 `async_conversion` 队列
4. 立即返回任务 ID 给客户端
5. Worker 从队列中取出任务，执行转换
6. 客户端通过 GET 接口轮询任务状态
7. 任务完成或失败后，客户端获取转换结果

***

## 3. 接口设计规范

### 3.1 通用规范

- **基础路径**：`/api`
- **请求格式**：`multipart/form-data`（文件上传）
- **响应格式**：`application/json`
- **字符编码**：UTF-8
- **超时说明**：同步接口建议客户端设置 300 秒超时；异步接口无超时限制

### 3.2 接口列表

| 接口路径                   | 方法   | 说明        | 模式 |
| ---------------------- | ---- | --------- | -- |
| `/api/convert`         | POST | 同步文档转换    | 同步 |
| `/api/tasks`           | POST | 异步提交转换任务  | 异步 |
| `/api/tasks/{task_id}` | GET  | 查询任务状态和结果 | 异步 |
| `/api/health`          | GET  | 健康检查      | -  |
| `/api/formats`         | GET  | 查询支持的文件格式 | -  |

### 3.3 同步转换接口

**请求参数**：

| 参数名              | 类型      | 必填 | 默认值      | 说明                                                            |
| ---------------- | ------- | -- | -------- | ------------------------------------------------------------- |
| `file`           | File    | 是  | -        | 要转换的文档文件，最大 50MB                                              |
| `enable_ocr`     | Boolean | 否  | `false`  | 是否开启 OCR 图转文（服务端预配置 Tesseract）                                |
| `enable_llm`     | Boolean | 否  | `false`  | 是否开启大模型图片描述（服务端预配置模型）                                         |
| `image_mode`     | String  | 否  | `base64` | 图片处理模式：`base64`（嵌入Base64）、`placeholder`（占位符）、`external`（外部链接） |
| `image_quality`  | Integer | 否  | `100`    | 图片压缩质量（1-100），100 表示不压缩                                       |
| `max_image_size` | Integer | 否  | `-1`     | 图片最长边限制（像素），-1 表示不限制                                          |

**响应参数**：

| 参数名             | 类型      | 说明               |
| --------------- | ------- | ---------------- |
| `code`          | Integer | 状态码，200 表示成功     |
| `message`       | String  | 提示信息             |
| `data.filename` | String  | 原文件名             |
| `data.markdown` | String  | 转换后的 Markdown 内容 |
| `data.images`   | Array   | 提取到的图片列表         |
| `data.duration` | Float   | 转换耗时（秒）          |

**请求示例**：

```bash
curl -X POST http://localhost:5926/api/convert \
  -F "file=@文档.pdf" \
  -F "enable_ocr=true" \
  -F "image_mode=placeholder"
```

**响应示例**（`image_mode=external`）：

```json
{
  "code": 200,
  "message": "转换成功",
  "data": {
    "filename": "文档.pdf",
    "markdown": "# 文档标题\n\n![用户登录流程图](images/image_001.png)",
    "images": [
      {
        "name": "image_001.png",
        "content": "data:image/png;base64,iVBORw0KGgo...",
        "width": 1920,
        "height": 1080
      }
    ],
    "duration": 3.25
  }
}
```

### 3.4 异步转换接口

**请求参数**：

| 参数名            | 类型      | 必填 | 默认值      | 说明                                       |
| -------------- | ------- | -- | -------- | ---------------------------------------- |
| `file`         | File    | 是  | -        | 要转换的文档文件，最大 50MB                         |
| `enable_ocr`   | Boolean | 否  | `false`  | 是否开启 OCR 图转文（服务端预配置）                     |
| `enable_llm`   | Boolean | 否  | `false`  | 是否开启大模型图片描述（服务端预配置）                      |
| `image_mode`   | String  | 否  | `base64` | 图片处理模式：`base64`、`placeholder`、`external` |
| `image_quality`  | Integer | 否  | `100`    | 图片压缩质量（1-100），100 表示不压缩                                       |
| `max_image_size` | Integer | 否  | `-1`     | 图片最长边限制（像素），-1 表示不限制                                          |
| `callback_url` | String  | 否  | `null`   | 任务完成后的回调通知地址                             |

**响应参数**：

| 参数名              | 类型      | 说明            |
| ---------------- | ------- | ------------- |
| `code`           | Integer | 状态码，200 表示成功  |
| `message`        | String  | 提示信息          |
| `data.task_id`   | String  | 任务唯一标识        |
| `data.status`    | String  | 任务状态：`queued` |
| `data.query_url` | String  | 查询任务状态的接口地址   |

**请求示例**：

```bash
curl -X POST http://localhost:5926/api/tasks \
  -F "file=@大文档.pdf" \
  -F "enable_ocr=true" \
  -F "image_mode=external" \
  -F "callback_url=http://your-server.com/webhook"
```

**响应示例**：

```json
{
  "code": 200,
  "message": "任务已提交",
  "data": {
    "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "status": "queued",
    "query_url": "/api/tasks/a1b2c3d4-e5f6-7890-abcd-ef1234567890"
  }
}
```

### 3.5 任务查询接口

**请求参数**：

| 参数名       | 类型     | 必填   | 说明    |
| --------- | ------ | ---- | ----- |
| `task_id` | String | 路径参数 | 任务 ID |

**响应参数**：

| 参数名             | 类型      | 说明                                                    |
| --------------- | ------- | ----------------------------------------------------- |
| `code`          | Integer | 状态码                                                   |
| `data.task_id`  | String  | 任务 ID                                                 |
| `data.status`   | String  | 任务状态：`queued` / `processing` / `completed` / `failed` |
| `data.progress` | Integer | 进度百分比（0-100）                                          |
| `data.filename` | String  | 原文件名                                                  |
| `data.result`   | Object  | 转换结果（仅 `completed` 状态时返回）                             |
| `data.error`    | String  | 错误信息（仅 `failed` 状态时返回）                                |

**状态流转**：

```
queued → processing → completed
                    → failed
```

**响应示例（处理中）**：

```json
{
  "code": 200,
  "data": {
    "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "status": "processing",
    "progress": 45,
    "filename": "大文档.pdf"
  }
}
```

**请求示例**：

```bash
curl http://localhost:5926/api/tasks/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

**响应示例（已完成）**：

```json
{
  "code": 200,
  "data": {
    "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "status": "completed",
    "progress": 100,
    "filename": "大文档.pdf",
    "result": {
      "markdown": "# 转换后的内容...\n\n![流程图](images/img1.png)",
      "images": [
        {
          "name": "img1.png",
          "content": "data:image/png;base64,iVBORw0KGgo...",
          "width": 1920,
          "height": 1080
        }
      ]
    }
  }
}
```

**响应示例（失败）**：

```json
{
  "code": 200,
  "data": {
    "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "status": "failed",
    "progress": 0,
    "filename": "损坏的文档.pdf",
    "error": "文件损坏或格式不支持"
  }
}
```

### 3.6 健康检查接口

**请求示例**：

```bash
curl http://localhost:5926/api/health
```

**响应示例**：

```json
{
  "status": "ok",
  "service": "Doc2MarkAPI 文档转换服务",
  "version": "1.0.0",
  "uptime": 3600
}
```

**状态说明**：
- `ok`：服务正常运行，Redis 连接正常
- `degraded`：服务运行但 Redis 连接异常（异步任务功能可能受影响）

### 3.7 查询支持的文件格式接口

**请求示例**：

```bash
curl http://localhost:5926/api/formats
```

**响应示例**：

```json
{
  "code": 200,
  "message": "success",
  "data": {
    "document": [
      "pdf",
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
      "enabled": true,
      "language": "chi_sim+eng",
      "description": "OCR 图转文功能，提取图片中的文字内容"
    },
    "llm": {
      "enabled": true,
      "model": "Qwen3.6-Flash",
      "description": "多模态大模型图片描述功能"
    }
  }
}
```

### 3.8 错误码规范

| 状态码   | 含义           | 说明                 |
| ----- | ------------ | ------------------ |
| `200` | 成功           | 请求处理成功             |
| `413` | 文件过大         | 超过 50MB 大小限制       |
| `415` | 不支持的媒体类型    | 文件格式不支持            |
| `404` | 任务不存在       | 查询的任务 ID 无效        |
| `500` | 服务器内部错误     | 转换过程中出现异常          |

***

## 4. 图片处理策略

### 4.1 图片处理模式

通过 `image_mode` 参数控制图片处理方式，支持以下模式：

| `image_mode` 值 | 输出示例                               | 说明                         | 适用场景          |
| -------------- | ---------------------------------- | -------------------------- | ------------- |
| `base64`       | `![描述](data:image/png;base64,...)` | 将图片编码为 Base64 嵌入 Markdown  | 本地笔记、单文件分发    |
| `placeholder`  | `[图片：用户登录流程图]`                     | 将图片替换为占位符文本                | Dify 知识库、语义检索 |
| `external`     | `![描述](images/image_001.png)`      | Markdown 引用外部图片路径，图片文件单独返回 | Web 预览、需要保留原图 |

### 4.2 图片输出结构

每张图片的输出包含三个独立区块，可根据启用的功能灵活组合：

```markdown
---

# OCR 块（仅 enable_ocr=true 时输出）
**[OCR 识别 - 图片名]**
OCR 提取的文字内容...

# LLM 描述块（仅 enable_llm=true 时输出）
**[LLM 描述 - 图片名]**
LLM 生成的图片描述...

# 图片块（始终输出）
**[图片 - 图片名]**
![图片名](base64/占位符/外部链接)

---
```

### 4.3 功能独立性说明

三个功能完全独立，互不影响：

| 功能         | 控制参数         | 是否独立 | 输出条件                    |
| ---------- | ------------ | ---- | ----------------------- |
| **图片提取**   | `image_mode` | ✅ 独立 | 始终输出                    |
| **OCR 识别** | `enable_ocr` | ✅ 独立 | 仅 `enable_ocr=true` 时输出 |
| **LLM 描述** | `enable_llm` | ✅ 独立 | 仅 `enable_llm=true` 时输出 |

**组合示例：**

| enable\_ocr | enable\_llm | 输出内容                  |
| ----------- | ----------- | --------------------- |
| false       | false       | 仅图片块                  |
| true        | false       | OCR 块 + 图片块           |
| false       | true        | LLM 描述块 + 图片块         |
| true        | true        | OCR 块 + LLM 描述块 + 图片块 |

**`base64`** **模式**：

```json
{
  "data": {
    "markdown": "![描述](data:image/png;base64,...)",
    "images": []
  }
}
```

**`placeholder`** **模式**：

```json
{
  "data": {
    "markdown": "[图片：用户登录流程图]",
    "images": []
  }
}
```

**`external`** **模式**（同步接口）：

```json
{
  "data": {
    "markdown": "![描述](images/image_001.png)",
    "images": [
      {
        "name": "image_001.png",
        "content": "data:image/png;base64,iVBORw0KGgo...",
        "width": 1920,
        "height": 1080
      }
    ]
  }
}
```

**`external`** **模式**（异步接口）：

```json
{
  "data": {
    "result": {
      "markdown": "![描述](images/image_001.png)",
      "images": [
        {
          "name": "image_001.png",
          "content": "data:image/png;base64,iVBORw0KGgo...",
          "width": 1920,
          "height": 1080
        }
      ]
    }
  }
}
```

### 4.3 多模态图片描述

当开启 `enable_llm` 参数时，服务端会调用预配置的大模型生成图片的语义描述：

**输出示例**：

```markdown
![用户登录流程图](image_001.png)

> **AI 描述**：此流程图展示了用户登录的完整流程。用户首先输入账号密码，系统验证身份后，若验证通过则进入主页，若失败则返回登录页面并提示错误信息。
```

### 4.4 OCR 结果校验策略

#### 4.4.1 核心原则

**OCR 和** **`image_mode`** **是两个独立的处理维度：**

- **OCR 功能**：负责从图片中提取文字内容，输出到 Markdown 正文
- **图片模式**：负责处理图片本身（嵌入/占位符/描述/外部链接）
- **两者独立工作**：无论图片是否有文字，都会根据 `image_mode` 处理图片

#### 4.4.2 容错处理

**Tesseract OCR 行为特性：**

- **不会报错**：对任何图片都会尝试识别，包括纯照片、图表、图标等
- **返回空结果**：如果图片中没有可识别的文字，OCR 会返回空字符串或置信度极低的乱码
- **不中断流程**：OCR 识别失败不会影响后续的图片模式处理

#### 4.4.3 校验规则

为避免将无效文字输出到 Markdown，需要对 OCR 结果进行校验：

| 校验维度      | 判断标准                           | 处理方式            |
| --------- | ------------------------------ | --------------- |
| **文本长度**  | 识别出的文字少于 3 个字符                 | 视为无效，跳过 OCR 输出  |
| **字符置信度** | 平均置信度 < 60%                    | 视为无效，跳过 OCR 输出  |
| **字符合理性** | 包含大量乱码符号（如 `@#$%^&*` 占比 > 50%） | 视为无效，跳过 OCR 输出  |
| **有效文字**  | 通过上述校验                         | 输出到 Markdown 正文 |

#### 4.4.4 输出逻辑伪代码

```python
# OCR 处理流程
ocr_text = run_ocr(image)

# 校验 OCR 结果
if is_valid_ocr_result(ocr_text, min_length=3, min_confidence=0.6):
    # 有效文字，输出到 Markdown
    markdown += f"\n\n{ocr_text}\n\n"
else:
    # 无有效文字，跳过 OCR 输出（不报错）
    log.info(f"图片 {image.name} 未识别出有效文字，跳过 OCR 输出")

# 继续根据 image_mode 处理图片本身
markdown += format_image_by_mode(image, image_mode)
```

#### 4.4.5 输出示例

**场景 1：图片包含文字**\
参数：`enable_ocr=true` + `image_mode=placeholder`

```markdown
这是文档的正文内容...

系统架构图：前端 → 后端 → 数据库
（OCR 提取的文字内容）

[图片：系统架构图]
（placeholder 占位符）

这是文档的后续内容...
```

**场景 2：图片没有文字（照片/纯图形）**\
参数：`enable_ocr=true` + `image_mode=placeholder`

```markdown
这是文档的正文内容...

[图片：风景照片]
（只有占位符，没有 OCR 输出，因为图片中无文字）

这是文档的后续内容...
```

**场景 3：图片没有文字 +** **`image_mode=base64`**\
参数：`enable_ocr=true` + `image_mode=base64`

```markdown
这是文档的正文内容...

![图片](data:image/png;base64,...)
（只有 Base64 图片，没有 OCR 输出）

这是文档的后续内容...
```

#### 4.4.6 校验规则可配置化

OCR 校验规则通过环境变量可配置：

| 环境变量                   | 类型      | 默认值 | 说明        |
| ---------------------- | ------- | --- | --------- |
| `OCR_MIN_LENGTH`       | Integer | 3   | 最小识别文字长度  |
| `OCR_MIN_CONFIDENCE`   | Float   | 0.6 | 最小字符置信度阈值 |
| `OCR_MAX_SYMBOL_RATIO` | Float   | 0.5 | 乱码符号最大占比  |

### 4.5 图片压缩优化

对于包含大量图片的文档，系统会自动对图片进行压缩处理：

**压缩策略**：

1. **尺寸压缩**：如果图片最长边超过 `max_image_size`，按比例缩放
2. **质量压缩**：一律转为 JPEG 格式，使用 `image_quality` 作为压缩质量
3. **智能选择**：如果 JPEG 结果 >= 原图大小，则保留原图（不压缩）

```python
from PIL import Image

def compress_image(image_bytes: bytes, image_quality: int, max_image_size: int) -> tuple:
    """压缩图片，返回 (压缩后字节, 格式)"""
    if image_quality == 100 and max_image_size == -1:
        return image_bytes, 'png'

    img = Image.open(io.BytesIO(image_bytes))
    original_size = len(image_bytes)

    if max_image_size > 0:
        width, height = img.size
        if max(width, height) > max_image_size:
            ratio = max_image_size / max(width, height)
            img = img.resize((int(width * ratio), int(height * ratio)), Image.LANCZOS)

    output = io.BytesIO()
    if img.format == 'PNG':
        img = img.convert('RGB')
    img.save(output, format='JPEG', quality=image_quality)
    compressed = output.getvalue()

    if len(compressed) < original_size:
        return compressed, 'jpeg'
    else:
        return image_bytes, 'png'
```

**压缩效果**：

| 场景            | 压缩前      | 压缩后     | 说明                |
| ------------- | -------- | ------- | ----------------- |
| 文字扫描件（原图高度压缩） | PNG 原图   | PNG 原图  | JPEG 比 PNG 大，保留原图 |
| 彩色照片          | JPEG 原图  | JPEG 压缩 | 压缩后更小             |
| 普通图片          | PNG/JPEG | JPEG 压缩 | 自动选择更小的格式         |

### 4.6 图片处理决策树

```
文档是否包含图片？
├── 否 → 直接转换，无需特殊处理
└── 是 → 处理每张图片
    ├── enable_ocr=true?
    │   └── 是 → 执行 OCR 识别，输出 OCR 块
    ├── enable_llm=true?
    │   └── 是 → 调用 LLM 生成描述，输出 LLM 描述块
    └── 选择图片处理模式 (image_mode)
        ├── base64 → Base64 嵌入 Markdown
        ├── placeholder → 替换为 [图片：描述] 占位符
        └── external → 外部链接模式
            ├── 同步接口 → 图片 Base64 放入响应的 images 数组
            └── 异步接口 → 图片 Base64 放入响应的 images 数组
```

**说明**：OCR、LLM 和图片处理是三个独立维度，可任意组合。

***

## 5. 异步任务处理

### 5.1 任务状态模型

```
┌─────────┐      ┌────────────┐      ┌───────────┐
│ queued  │────→│ processing │────→│ completed │
└─────────┘      └────────────┘      └───────────┘
                      │
                      │ 异常
                      ▼
                 ┌────────┐
                 │ failed │
                 └────────┘
```

### 5.2 任务数据结构

```json
{
  "task_id": "uuid-string",
  "status": "queued | processing | completed | failed",
  "progress": 0,
  "filename": "原文件名",
  "temp_file_path": "/tmp/uuid.pdf",
  "enable_ocr": false,
  "enable_llm": false,
  "image_mode": "base64",
  "callback_url": null,
  "result": null,
  "error": null,
  "created_at": "2026-05-15T10:35:16",
  "completed_at": null
}
```

### 5.3 Worker 配置

| 配置项       | 推荐值                   | 说明              |
| --------- | --------------------- | --------------- |
| 同步 Worker 数量 | 2 个                | 专属处理同步任务，不被异步阻塞 |
| 异步 Worker 数量 | 4 个                | 处理异步任务，可按积压扩展 |
| 同步任务超时      | 300 秒                 | 同步接口等待超时时间      |
| 异步任务超时      | 21600 秒（6 小时）          | 异步任务最大执行时间，覆盖从纯文本到 OCR+LLM 全场景 |
| 同步队列名称      | `sync_conversion` | 同步任务队列名称          |
| 异步队列名称      | `async_conversion` | 异步任务队列名称          |

### 5.4 回调通知

当任务完成或失败时，如果客户端提供了 `callback_url`，Worker 会向该地址发送 POST 请求：

**任务完成：**

```json
{
  "task_id": "uuid-string",
  "status": "completed",
  "result": {
    "markdown": "# 转换后的内容...",
    "pages": 100,
    "images": ["img1.png"]
  }
}
```

**任务失败：**

```json
{
  "task_id": "uuid-string",
  "status": "failed",
  "error": "错误信息描述"
}
```

### 5.5 任务清理策略

#### 5.5.1 清理目的

异步任务会产生以下需要清理的资源：

- **任务元数据**：存储在 Redis 中的任务状态信息
- **临时文件**：上传的原始文件、转换过程中生成的中间文件
- **结果数据**：转换后的 Markdown 内容和图片数据

#### 5.5.2 清理规则

| 资源类型       | 清理时机  | 保留时间  | 说明          |
| ---------- | ----- | ----- | ----------- |
| **任务元数据**  | 定时清理  | 86400 秒（24 小时） | 任务完成/失败后保留  |
| **原始上传文件** | 任务完成后 | 立即    | Worker 使用 TemporaryDirectory 自动清理 |
| **中间文件**   | 任务完成后 | 立即    | 转换完成后立即删除   |
| **结果缓存**   | 定时清理  | 43200 秒（12 小时） | 供客户端查询的结果数据 |

#### 5.5.3 清理配置

| 配置项                        | 推荐值 | 说明             |
| --------------------------- | --- | -------------- |
| `TASK_TTL`                  | 86400  | 任务元数据保留时间（秒）   |
| `RESULT_TTL`                | 43200  | 转换结果保留时间（秒）    |
| `CLEANUP_INTERVAL_MINUTES`  | 10  | 定时清理执行间隔（分钟）   |

#### 5.5.4 清理流程

```
┌─────────────────────────────────────────────────────────────┐
│                    定时清理任务 (每 10 分钟)                  │
└────────────────────────────┬────────────────────────────────┘
                             │
        ┌────────────────────┴────────────────────┐
        ▼                                        ▼
┌───────────────┐                         ┌───────────────┐
│ 清理过期任务  │                         │ 清理过期结果  │
│ 元数据        │                         │ 缓存          │
└───────────────┘                         └───────────────┘
        │                                        │
        ▼                                        ▼
   Redis 删除                                Redis 删除
```

#### 5.5.5 清理脚本示例

```python
from datetime import datetime, timedelta
import redis

def cleanup_expired_tasks(redis_client, task_ttl=86400):
    """清理过期的任务元数据"""
    cutoff_time = (datetime.now() - timedelta(seconds=task_ttl)).timestamp()
    keys = redis_client.keys("task:*")
    for key in keys:
        created_at = redis_client.hget(key, "created_at")
        if created_at and float(created_at) < cutoff_time:
            redis_client.delete(key)
```

#### 5.5.6 容器化清理配置

在 `docker-compose.yml` 中添加清理相关环境变量：

```yaml
environment:
  # 清理配置
  - TASK_TTL=86400
  - RESULT_TTL=43200
  - CLEANUP_INTERVAL_MINUTES=10
```

***

## 6. 性能优化

### 6.1 不同配置下的性能对比

| 配置模式          | 每张图片耗时   | 100页文档（每页1张图）      | 适用场景          |
| ------------- | -------- | ------------------ | ------------- |
| 纯文本提取（无OCR）   | 0.1-0.5秒 | 10-50秒             | 电子版PDF，无需处理图片 |
| 开启OCR（图转文）    | 1-5秒     | 100-500秒（约2-8分钟）   | 扫描件，只需提取文字    |
| 开启大模型（图片描述）   | 3-10秒    | 300-1000秒（约5-17分钟） | 需要AI理解图片内容    |
| OCR + 大模型同时开启 | 4-15秒    | 400-1500秒（约7-25分钟） | 需要完整图文理解      |

### 6.2 性能优化建议

| 优化项    | 建议配置                                     | 预期效果             |
| ------ | ---------------------------------------- | ---------------- |
| 图片压缩   | `max_image_size=800`, `image_quality=80` | 减少 50%+ 图片体积     |
| OCR 并行 | 启用多线程处理                                  | 提升 30-50% OCR 速度 |
| 缓存机制   | 缓存已转换文档的哈希                               | 重复文档秒级响应         |
| 异步模式   | 大文件使用 `/api/tasks`                       | 避免超时             |

***

## 7. 部署方案

### 7.1 Docker 容器化部署（推荐）

项目采用三容器架构：**API 服务** + **Worker 服务** + **Redis**，通过 Docker Compose 编排管理。Worker 容器内部使用 multiprocessing 启动两组进程，分别消费同步队列和异步队列。

#### 7.1.1 容器架构

```
┌─────────────────────────────────────────────────────────────┐
│                   Docker Compose                            │
│  ┌─────────────┐  ┌──────────────────────────┐  ┌────────┐ │
│  │  API 服务   │  │      Worker 服务         │  │ Redis  │ │
│  │  (FastAPI)  │  │  ┌─────────┐ ┌────────┐ │  │(双队列)│ │
│  │  Port:5926  │  │  │Sync ×2  │ │Async×4 │ │  │Port:   │ │
│  │             │  │  │(BLPOP等 │ │(RQ消费)│ │  │6379    │ │
│  └──────┬──────┘  │  │待结果)  │ │        │ │  └───┬────┘ │
│         │         │  └─────────┘ └────────┘ │      │      │
│         │         └──────────────┬───────────┘      │      │
│         └───────────────────────┴──────────────────┘      │
│                      内部网络通信                           │
└─────────────────────────────────────────────────────────────┘

队列说明：
  - sync_conversion：同步任务专用队列，API 提交后 BLPOP 阻塞等待结果
  - async_conversion：异步任务队列，Worker 消费后结果存 Redis，API 轮询查询
```

#### 7.1.2 Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# 使用清华源加速 apt 包下载
RUN printf 'Types: deb\nURIs: https://mirrors.tuna.tsinghua.edu.cn/debian\nSuites: trixie trixie-updates trixie-backports\nComponents: main contrib non-free non-free-firmware\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n\nTypes: deb\nURIs: https://mirrors.tuna.tsinghua.edu.cn/debian-security\nSuites: trixie-security\nComponents: main contrib non-free non-free-firmware\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' > /etc/apt/sources.list.d/debian.sources

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    tesseract-ocr-eng \
    exiftool \
    poppler-utils \
    libreoffice \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖（使用清华源）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制应用代码
COPY app/ ./app/

# 创建必要的目录
RUN mkdir -p temp logs

# 暴露服务端口
EXPOSE 5926

# 启动命令在 docker-compose.yml 中定义
```

#### 7.1.3 docker-compose.yml

```yaml
version: '3.8'

name: doc2mark-api

services:
  api:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    container_name: doc2mark-api-api
    ports:
      - "5926:5001"
    env_file:
      - .env
    volumes:
      - ./volumes/logs:/app/logs
      - ./volumes/temp:/app/temp
    depends_on:
      - redis
    restart: unless-stopped
    command: python -m app.main

  worker:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    container_name: doc2mark-api-worker
    env_file:
      - .env
    volumes:
      - ./volumes/logs:/app/logs
      - ./volumes/temp:/app/temp
    depends_on:
      - redis
    restart: unless-stopped
    command: python -m app.run_worker

  redis:
    image: redis:8.6.2
    container_name: doc2mark-api-redis
    volumes:
      - ./volumes/redis:/data
    restart: unless-stopped

volumes:
  redis_data:
```

**容器说明**：

| 容器名                  | 服务     | 端口   | 说明                    |
| -------------------- | ------ | ---- | --------------------- |
| `doc2mark-api-api`   | API    | 5926 | FastAPI 应用，处理同步请求和健康检查 |
| `doc2mark-api-worker`| Worker | -    | RQ 工作节点，处理异步转换任务      |
| `doc2mark-api-redis` | Redis  | 6379 | 任务队列和数据缓存（内部使用）       |

#### 7.1.4 环境变量配置说明

在 `docker/.env` 文件中配置（完整参数见 `.env.example`）：

| 环境变量 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `SERVICE_PORT` | Integer | 5926 | 服务监听端口 |
| `LOG_LEVEL` | String | INFO | 日志级别 |
| `REDIS_HOST` | String | redis | Redis 主机地址 |
| `REDIS_PORT` | Integer | 6379 | Redis 端口 |
| `REDIS_DB` | Integer | 0 | Redis 数据库编号 |
| `LLM_BASE_URL` | String | - | 大模型 API 地址 |
| `LLM_API_KEY` | String | - | 大模型 API Key |
| `LLM_MODEL` | String | - | 模型名称 |
| `ENABLE_LLM` | Boolean | true | 是否启用大模型 |
| `OCR_ENABLED` | Boolean | true | 是否启用 OCR |
| `OCR_LANGUAGE` | String | chi_sim+eng | OCR 语言包 |
| `MAX_REQUESTS_PER_MINUTE` | Integer | 60 | 请求限流（每分钟最大请求数） |
| `MAX_FILE_SIZE` | Integer | 52428800 | 最大文件大小（字节） |
| `ALLOWED_ORIGINS` | String | * | 允许的跨域来源 |

#### 7.1.5 部署步骤

**步骤一：进入 docker 目录**

```bash
cd docker
```

**步骤二：配置环境变量**

```bash
# 复制环境变量模板
cp .env.example .env

# 编辑 .env 文件，配置必要参数
# 至少需要配置 LLM_BASE_URL、LLM_API_KEY、LLM_MODEL
```

**.env 配置示例**：

```bash
# 服务配置
SERVICE_PORT=5926
LOG_LEVEL=INFO

# Redis 配置
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0

# 大模型配置（必填）
LLM_BASE_URL=https://your-api-server.com/v1
LLM_API_KEY=sk-your-api-key-here
LLM_MODEL=Qwen3.6-Flash

# 可选配置
ENABLE_LLM=true
OCR_ENABLED=true
OCR_LANGUAGE=chi_sim+eng
MAX_REQUESTS_PER_MINUTE=60
MAX_FILE_SIZE=52428800
```

**步骤三：启动服务**

```bash
# 构建并启动所有容器
docker-compose up -d --build

# 查看启动日志
docker-compose logs -f
```

**步骤四：验证服务**

```bash
# 检查健康状态
curl http://localhost:5926/api/health

# 查看支持的格式
curl http://localhost:5926/api/formats

# 测试同步转换
curl -X POST http://localhost:5926/api/convert \
  -F "file=@test.pdf" \
  -F "image_mode=base64"
```

#### 7.1.6 服务管理

**查看容器状态**：

```bash
docker-compose ps
```

**查看日志**：

```bash
# 查看所有容器日志
docker-compose logs -f

# 查看特定容器日志
docker-compose logs -f api
docker-compose logs -f worker
```

**重启服务**：

```bash
docker-compose restart
```

**停止服务**：

```bash
docker-compose down
```

**清理数据**：

```bash
# 停止并删除容器和网络（保留数据卷）
docker-compose down

# 删除数据卷（谨慎操作，会清空所有数据）
docker-compose down -v
```

### 7.2 端口说明

| 端口   | 服务     | 说明                     |
| ---- | ------ | ---------------------- |
| 5926 | API 服务 | 对外暴露的唯一服务端口，所有 HTTP 请求由此进入 |
| 6379 | Redis  | 内部通信，不对外暴露          |

### 7.3 目录结构

```
doc2mark-api/
├── app/                    # 应用代码
│   ├── api/                # API 路由和中间件
│   ├── services/           # 核心服务（转换、OCR、LLM）
│   ├── workers/            # 异步任务
│   ├── main.py             # 入口
│   ├── config.py           # 配置
│   ├── models.py           # 数据模型
│   └── run_worker.py       # Worker 入口
├── docker/                 # Docker 配置
│   ├── Dockerfile          # 生产镜像
│   ├── docker-compose.yml  # 编排配置
│   ├── .env.example        # 环境变量模板
│   └── volumes/            # 数据挂载目录
├── requirements.txt        # Python 依赖
└── .dockerignore           # Docker 忽略文件
```

### 7.4 数据卷挂载

| 宿主机目录 | 容器目录 | 说明 |
|----------|----------|------|
| `./volumes/logs` | `/app/logs` | 日志文件存储 |
| `./volumes/temp` | `/app/temp` | 临时文件存储 |
| `./volumes/redis` | `/data` | Redis 数据持久化 |

***

## 8. 安全与监控

### 8.1 安全策略

#### 8.1.1 文件上传安全

| 安全措施    | 说明                | 实现方式             |
| ------- | ----------------- | ---------------- |
| 文件类型白名单 | 只允许上传指定类型的文件      | 校验文件扩展名            |
| 文件大小限制  | 限制单个文件最大 50MB     | 在接收阶段进行大小检查      |
| 文件重命名   | 上传后随机重命名，防止路径遍历攻击 | 使用 UUID 生成新文件名   |
| 临时文件隔离  | 上传文件存储在隔离目录       | 限制目录权限，定期清理      |

#### 8.1.2 API 安全（内部小团队简化版）

| 安全措施   | 说明         | 实现方式               | 默认状态 |
| ------ | ---------- | ------------------ | ---- |
| 请求限流   | 限制单客户端请求频率 | 使用 Redis 实现令牌桶算法   | 启用   |
| 跨域访问控制 | 限制允许的来源域名  | 配置 CORS 白名单        | 启用   |
| 输入参数校验 | 防止注入攻击     | 使用 Pydantic 进行参数校验 | 启用   |

**安全配置示例**：

```yaml
environment:
  # API 安全配置（内部团队简化版）
  - MAX_REQUESTS_PER_MINUTE=60  # 请求限流（每分钟最大请求数）
  - ALLOWED_ORIGINS=*           # 允许所有来源（内网环境）
```

**请求示例**：

```bash
# 内部团队直接访问，无需认证
curl -X POST http://localhost:5926/api/convert \
  -F "file=@document.pdf"
```

#### 8.1.3 数据安全

| 安全措施   | 说明                     | 实现方式         |
| ------ | ---------------------- | ------------ |
| 敏感信息保护 | 大模型 API Key 等敏感信息不记录日志 | 使用环境变量管理敏感配置 |
| 访问日志脱敏 | 日志中不记录文件内容和 Base64 数据  | 日志输出时进行脱敏处理  |

### 8.2 监控体系（内网方案）

#### 8.2.1 监控架构

```
┌─────────────────────────────────────────────────────────────┐
│                      内网监控架构                           │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│   │   API 服务   │    │   Redis      │    │   定时任务   │  │
│   │  (FastAPI)   │    │              │    │  (清理/监控) │  │
│   └──────┬───────┘    └──────┬───────┘    └──────┬───────┘  │
│          │                   │                   │          │
│          ▼                  ▼                  ▼         │
│   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│   │   日志文件   │    │   指标数据   │    │   告警记录   │  │
│   │  (JSON格式)  │    │  (Redis)     │    │  (本地存储)  │  │
│   └──────┬───────┘    └──────┬───────┘    └──────┬───────┘  │
│          │                   │                   │          │
│          └──────────┬────────┴───────────────────┘          │
│                     ▼                                      │
│          ┌──────────────────────┐                           │
│          │    监控仪表盘接口    │ ← 内网可访问的监控界面   │
│          └──────────────────────┘                           │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

#### 8.2.2 日志记录

| 日志类型 | 记录内容              | 存储方式                      |
| ---- | ----------------- | ------------------------- |
| 应用日志 | 全量应用日志（INFO 及以上）    | JSON 文件 → logs/app.log   |
| 访问日志 | 请求时间、路径、方法、状态码、耗时 | JSON 文件 → logs/access.log |
| 错误日志 | 异常堆栈、错误信息、请求上下文   | JSON 文件 → logs/error.log  |

#### 8.2.3 指标监控

| 指标类型  | 监控内容             | 告警阈值      |
| ----- | ---------------- | --------- |
| 服务可用性 | API 成功率          | < 95% 告警  |
| 响应时间  | P50/P95/P99 响应时间 | 见下方动态阈值策略 |
| 吞吐量   | 每秒请求数（QPS）       | 超过系统容量告警  |
| 资源使用  | CPU、内存、磁盘使用率     | > 80% 告警  |
| 队列状态  | 任务队列长度           | > 100 告警  |

#### 8.2.4 动态响应时间阈值策略

考虑到文档转换任务的特殊性（大文件、复杂处理），响应时间阈值需要根据任务配置动态调整：

| 任务配置      | 同步接口超时时间 | P95 告警阈值 | 说明         |
| --------- | -------- | -------- | ---------- |
| 纯文本提取     | 30 秒     | 20 秒     | 无图片处理，速度最快 |
| 仅 OCR     | 120 秒    | 60 秒     | 图片转文字，中等耗时 |
| 仅大模型      | 180 秒    | 120 秒    | AI 图片描述，较慢 |
| OCR + 大模型 | 300 秒    | 200 秒    | 完整处理，最慢    |

**监控策略**：

1. **按任务类型分类统计**：分别记录不同配置任务的响应时间分布
2. **自适应阈值调整**：根据历史数据动态调整告警阈值
3. **超时分离**：同步接口设为硬超时，异步任务不设响应时间告警，改为监控任务执行时长

**异步任务执行时长监控**：

| 任务配置      | 正常执行时间范围 | 告警阈值     |
| --------- | -------- | -------- |
| 纯文本提取     | < 60 秒   | > 120 秒  |
| 仅 OCR     | < 300 秒  | > 600 秒  |
| 仅大模型      | < 600 秒  | > 1200 秒 |
| OCR + 大模型 | < 1200 秒 | > 1800 秒 |

**响应时间监控指标结构**：

```json
{
  "response_time": {
    "sync": {
      "all": { "p50": 5000, "p95": 15000, "p99": 25000 },
      "text_only": { "p50": 3000, "p95": 10000, "p99": 20000 },
      "with_ocr": { "p50": 10000, "p95": 40000, "p99": 80000 },
      "with_llm": { "p50": 30000, "p95": 100000, "p99": 150000 },
      "ocr_plus_llm": { "p50": 60000, "p95": 180000, "p99": 280000 }
    },
    "async": {
      "avg_duration_ms": 120000,
      "max_duration_ms": 300000,
      "pending_tasks": 5
    }
  }
}
```

#### 8.2.5 健康检查接口

```
GET /api/health
```

**响应示例**：

```json
{
  "status": "ok",
  "service": "Doc2MarkAPI 文档转换服务",
  "version": "1.0.0",
  "uptime": 3600,
  "dependencies": {
    "redis": "healthy",
    "tesseract": "healthy",
    "llm": "healthy"
  },
  "metrics": {
    "requests_total": 1234,
    "errors_total": 5,
    "avg_response_time": 1250
  }
}
```

#### 8.2.6 内网告警机制

由于内网环境无法访问外部通知服务，采用以下告警方式：

| 告警类型  | 触发条件          | 通知方式           |
| ----- | ------------- | -------------- |
| 服务宕机  | 健康检查失败        | 日志标记 + 监控仪表盘告警 |
| 错误率飙升 | 错误率 > 5%      | 日志标记 + 监控仪表盘告警 |
| 响应超时  | P95 响应时间 > 5s | 日志标记 + 监控仪表盘告警 |
| 资源告警  | CPU/内存 > 80%  | 日志标记 + 监控仪表盘告警 |
| 队列积压  | 队列长度 > 100    | 日志标记 + 监控仪表盘告警 |

#### 8.2.7 监控查询接口

**获取统计指标**：

```
GET /api/metrics
```

**响应示例**：

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
    "alerts": [
      {
        "level": "warning",
        "message": "响应时间偏高 (P95: 3500ms)",
        "timestamp": "2026-05-15T10:30:00"
      }
    ]
  }
}
```

#### 8.2.8 内置监控面板（轻量级）

为方便运维人员直观查看，服务内置一个简易监控页面，无需额外部署：

**访问地址**：

```
GET /monitor
```

**监控面板特性**：

| 功能   | 说明               |
| ---- | ---------------- |
| 实时状态 | 服务状态、运行时长、版本信息   |
| 请求统计 | 总请求数、今日请求、成功率    |
| 性能指标 | P50/P95/P99 响应时间 |
| 资源使用 | CPU、内存、磁盘使用率     |
| 队列状态 | 待处理/处理中任务数       |
| 告警展示 | 最近告警列表           |
| 自动刷新 | 每 30 秒自动更新       |

**监控面板界面示意**：

```
┌─────────────────────────────────────────────────────────────┐
│              Doc2MarkAPI 监控面板                           │
├─────────────────────────────────────────────────────────────┤
│ 状态:    运行中 | 版本: 1.0.0 | 运行时长: 2天 3小时         │
├─────────────────┬─────────────────┬─────────────────────────┤
│ 请求统计        │ 性能指标        │ 资源使用                │
│ ─────────────── │ ─────────────── │ ─────────────────────── │
│ 总请求: 12,345  │ P50: 800ms      │ CPU: 45%                │
│ 今日: 156       │ P95: 3,500ms    │ 内存: 62%               │
│ 成功率: 99.2%   │ P99: 8,000ms    │ 磁盘: 38%               │
├─────────────────────────────────────────────────────────────┤
│ 队列状态                                                    │
│ ─────────────────────────────────────────────────────────── │
│ 待处理: 5 | 处理中: 2 | 队列积压: 正常                      │
├─────────────────────────────────────────────────────────────┤
│ 最近告警                                                    │
│ ─────────────────────────────────────────────────────────── │
│    响应时间偏高 (P95: 3500ms) - 2026-05-15 10:30:00         │
└─────────────────────────────────────────────────────────────┘
```

**使用方式**：

1. 在内网浏览器访问 `http://localhost:5926/monitor`
2. 直接查看实时监控数据
3. 无需额外安装任何软件

#### 8.2.9 安全与监控配置（内部小团队简化版）

在 `docker-compose.yml` 中添加相关配置：

```yaml
environment:
  # 安全配置（内部团队简化版）
  - MAX_REQUESTS_PER_MINUTE=60  # 请求限流（每分钟最大请求数）
  - ALLOWED_ORIGINS=*           # 允许所有来源（内网环境）
  
  # 日志配置
  - LOG_LEVEL=INFO
  # 日志文件输出目录：logs/app.log（全量）、logs/error.log（错误）、logs/access.log（访问）
  
  # 任务配置
  - TASK_TTL=86400
  - RESULT_TTL=43200
  - CLEANUP_INTERVAL_MINUTES=10
  
  # Worker 配置
  - SYNC_TASK_TIMEOUT=300       # 同步任务等待超时（秒）
  - ASYNC_TASK_TIMEOUT=21600    # 异步任务执行超时（秒）
  - SYNC_WORKER_COUNT=2         # 同步 Worker 进程数
  - ASYNC_WORKER_COUNT=4        # 异步 Worker 进程数
  
  # 监控配置
  - ENABLE_METRICS=true
  - ENABLE_MONITOR_PANEL=true
  - MONITOR_REFRESH_INTERVAL=30  # 刷新间隔（秒）
  - ALERT_THRESHOLD_ERROR_RATE=5
  - ALERT_THRESHOLD_RESPONSE_TIME=5000
```

### 8.3 安全与监控最佳实践

| 实践项    | 说明                 |
| ------ | ------------------ |
| 定期日志清理 | 保留最近 30 天日志，定期压缩归档 |
| 敏感信息审计 | 定期检查日志中是否包含敏感信息    |
| 访问日志分析 | 定期分析访问模式，发现异常请求    |
| 告警规则调优 | 根据实际情况调整告警阈值       |
| 安全漏洞扫描 | 定期扫描依赖包安全漏洞        |

***

## 9. 附录

### 9.1 错误码汇总

| 状态码   | HTTP 状态 | 错误信息       | 说明               |
| ----- | ------- | ---------- | ---------------- |
| `200` | 成功      | `转换成功`     | 请求处理成功           |
| `400` | 请求错误    | `文件格式不支持`  | 上传的文件格式不在支持列表中   |
| `400` | 请求错误    | `未上传文件`    | 请求中没有包含文件        |
| `400` | 请求错误    | `参数错误`     | 请求参数格式不正确        |
| `413` | 文件过大    | `文件大小超过限制` | 文件大小超过 50MB 限制   |
| `404` | 任务不存在   | `任务 ID 无效` | 查询的任务 ID 不存在或已过期 |
| `500` | 服务器错误   | `转换失败`     | 转换过程中发生异常        |
| `503` | 服务不可用   | `服务暂时不可用`  | 服务正在维护或资源耗尽      |

### 9.2 环境变量配置汇总

#### 9.2.1 服务配置

| 环境变量           | 类型      | 默认值  | 说明     |
| -------------- | ------- | ---- | ------ |
| `SERVICE_PORT` | Integer | 5926 | 服务监听端口 |
| `LOG_LEVEL`    | String  | INFO | 日志级别   |

#### 9.2.2 Redis 配置

| 环境变量         | 类型      | 默认值   | 说明          |
| ------------ | ------- | ----- | ----------- |
| `REDIS_HOST` | String  | redis | Redis 主机地址  |
| `REDIS_PORT` | Integer | 6379  | Redis 端口    |
| `REDIS_DB`   | Integer | 0     | Redis 数据库编号 |

#### 9.2.3 大模型配置

| 环境变量                | 类型       | 默认值  | 说明              |
| ------------------- | -------- | ---- | --------------- |
| `ENABLE_LLM`        | Boolean  | false | 是否启用大模型         |
| `LLM_BASE_URL`      | String   | ""   | 大模型 API 基础 URL  |
| `LLM_API_KEY`       | String   | None | 大模型 API Key     |
| `LLM_MODEL`         | String   | ""   | 模型名称            |
| `LLM_PROMPT`        | String   | ""   | 用户提示词           |
| `LLM_SYSTEM_PROMPT` | String   | ""   | 系统提示词           |
| `LLM_MAX_TOKENS`    | Integer  | 16384 | 最大 Token 数     |
| `LLM_TEMPERATURE`   | Float    | 0.7  | 生成温度            |
| `LLM_TOP_P`         | Float    | 0.9  | Top-P 采样参数      |
| `LLM_TOP_K`         | Integer  | None | Top-K 采样参数      |
| `LLM_TIMEOUT`       | Integer  | 300  | 请求超时时间（秒）       |
| `LLM_STREAM`        | Boolean  | true | 是否流式输出          |
| `LLM_INCLUDE_USAGE` | Boolean  | true | 是否包含 Token 用量信息 |
| `LLM_EXTRA_BODY`    | String   | "{}" | 额外请求体参数（JSON）   |
| `LLM_EXTRA_PARAMS`  | String   | "{}" | 额外查询参数（JSON）    |

#### 9.2.4 OCR 配置

| 环境变量                  | 类型      | 默认值                | 说明                  |
| --------------------- | ------- | ------------------ | ------------------- |
| `OCR_ENABLED`         | Boolean | true               | 是否启用 OCR            |
| `OCR_LANGUAGE`        | String  | chi\_sim+eng       | OCR 语言包             |
| `OCR_TESSERACT_PATH`  | String  | None               | Tesseract 可执行文件路径   |
| `OCR_MIN_LENGTH`      | Integer | 3                  | OCR 结果最小长度          |
| `OCR_MIN_CONFIDENCE`  | Float   | 0.6                | OCR 置信度阈值           |
| `OCR_MAX_SYMBOL_RATIO` | Float | 0.5                | OCR 符号占比阈值          |

#### 9.2.5 图片处理配置

| 环境变量                     | 类型      | 默认值    | 说明                        |
| ------------------------ | ------- | ------ | ------------------------- |
| `DEFAULT_IMAGE_QUALITY`  | Integer | 100    | 默认图片压缩质量（1-100，100 表示不压缩） |
| `DEFAULT_MAX_IMAGE_SIZE` | Integer | -1     | 图片最长边限制（像素，-1 表示不限制）      |

#### 9.2.6 安全配置（内部小团队简化版）

| 环境变量                      | 类型      | 默认值        | 说明             |
| ------------------------- | ------- | ---------- | -------------- |
| `MAX_REQUESTS_PER_MINUTE` | Integer | 60         | 请求限流（每分钟最大请求数） |
| `ALLOWED_ORIGINS`         | String  | *          | 允许的跨域来源         |
| `MAX_FILE_SIZE`           | Integer | 52428800   | 最大文件大小（字节）     |

#### 9.2.7 清理配置

| 环境变量                       | 类型      | 默认值 | 说明         |
| -------------------------- | ------- | --- | ---------- |
| `TASK_TTL`                 | Integer | 86400  | 任务元数据保留时间（秒） |
| `RESULT_TTL`               | Integer | 43200  | 转换结果保留时间（秒）  |
| `CLEANUP_INTERVAL_MINUTES` | Integer | 10  | 定时清理执行间隔（分钟） |

#### 9.2.8 Worker 配置

| 环境变量                  | 类型      | 默认值  | 说明              |
| --------------------- | ------- | ---- | --------------- |
| `SYNC_TASK_TIMEOUT`   | Integer | 300  | 同步任务超时时间（秒）     |
| `ASYNC_TASK_TIMEOUT`  | Integer | 21600 | 异步任务超时时间（秒）    |
| `SYNC_WORKER_COUNT`   | Integer | 2    | 同步队列 Worker 进程数 |
| `ASYNC_WORKER_COUNT`  | Integer | 4    | 异步队列 Worker 进程数 |

#### 9.2.9 监控配置

| 环境变量                            | 类型      | 默认值  | 说明           |
| ------------------------------- | ------- | ---- | ------------ |
| `ENABLE_METRICS`                | Boolean | true | 是否启用指标收集     |
| `ENABLE_MONITOR_PANEL`          | Boolean | true | 是否启用监控面板     |
| `MONITOR_REFRESH_INTERVAL`      | Integer | 30   | 监控面板刷新间隔（秒）  |
| `ALERT_THRESHOLD_ERROR_RATE`    | Integer | 5    | 错误率告警阈值（%）   |
| `ALERT_THRESHOLD_RESPONSE_TIME` | Integer | 5000 | 响应时间告警阈值（毫秒） |

### 9.3 支持的文件格式

#### 9.3.1 文档格式

| 格式         | 扩展名             | 说明                        |
| ---------- | --------------- | ------------------------- |
| PDF        | `.pdf`          | 支持文本层和扫描件                 |
| Word       | `.doc`, `.docx` | Microsoft Word 文档         |
| PowerPoint | `.ppt`, `.pptx` | Microsoft PowerPoint 演示文稿 |
| Excel      | `.xls`, `.xlsx` | Microsoft Excel 电子表格      |

#### 9.3.2 图片格式

| 格式   | 扩展名             | 说明        |
| ---- | --------------- | --------- |
| JPEG | `.jpg`, `.jpeg` | 支持标准和渐进式  |
| PNG  | `.png`          | 支持透明背景    |
| GIF  | `.gif`          | 支持静态图片    |
| BMP  | `.bmp`          | 位图格式      |
| TIFF | `.tiff`         | 支持多页 TIFF |

#### 9.3.3 文本格式

| 格式       | 扩展名     | 说明          |
| -------- | ------- | ----------- |
| 纯文本      | `.txt`  | 普通文本文件      |
| Markdown | `.md`   | Markdown 格式 |
| HTML     | `.html` | HTML 网页     |
| XML      | `.xml`  | XML 文档      |

### 9.4 快速部署指南

#### 9.4.1 步骤一：创建配置文件

```bash
# 创建 .env 文件
cat > .env << EOF
LLM_API_KEY=sk-your-api-key-here
EOF
```

#### 9.4.2 步骤二：启动服务

```bash
# 构建并启动服务
docker-compose --env-file .env up -d
```

#### 9.4.3 步骤三：验证服务

```bash
# 检查服务状态
curl http://localhost:5926/api/health
```

#### 9.4.4 步骤四：测试转换

```bash
# 测试文档转换
curl -X POST http://localhost:5926/api/convert \
  -F "file=@test.pdf" \
  -F "image_mode=base64"
```

### 9.5 性能参考

| 配置模式      | 单页耗时      | 100页文档耗时   |
| --------- | --------- | ---------- |
| 纯文本提取     | 0.1-0.5 秒 | 10-50 秒    |
| 开启 OCR    | 1-5 秒/图   | 100-500 秒  |
| 开启 LLM    | 3-10 秒/图  | 300-1000 秒 |
| OCR + LLM | 4-15 秒/图  | 400-1500 秒 |

### 9.6 增强功能说明

#### 9.6.1 限流中间件

服务内置请求限流功能，防止 API 滥用：

**配置参数**：
| 参数名 | 默认值 | 说明 |
|--------|--------|------|
| `MAX_REQUESTS_PER_MINUTE` | 60 | 每分钟最大请求数 |

**实现逻辑**：
- 基于客户端 IP 地址进行限流统计
- 使用内存字典存储请求计数，自动清理过期数据
- 超出限制时返回 HTTP 429 状态码

**代码位置**：[middleware.py](../app/api/middleware.py)

#### 9.6.2 PDF 智能回退方案

当 MarkItDown 无法从 PDF 中提取文本内容时（如扫描件、图片型 PDF），系统自动启用 PyMuPDF 回退方案：

**工作流程**：
1. 尝试使用 MarkItDown 提取文本
2. 如果提取内容为空或仅包含空白字符，触发回退
3. 使用 PyMuPDF 将 PDF 每页渲染为灰度图片（150 DPI）
4. 对渲染的图片执行 OCR/LLM 处理（如果启用）

**配置参数**：
| 参数名 | 默认值 | 说明 |
|--------|--------|------|
| `PDF_FALLBACK_RENDER_PAGES` | true | 是否启用 PDF 回退渲染 |
| `PDF_PAGES_PER_IMAGE` | 6 | 将多少页 PDF 渲染为一张长图（当前实现为逐页渲染） |

**优化策略**：
- 使用灰度模式（`fitz.csGRAY`）大幅减小图片体积
- PNG 格式对灰度文字压缩效率极高
- 默认不压缩（quality=100），保持最佳质量

**代码位置**：[converter.py](../app/services/converter.py#L129-L197)

#### 9.6.3 静态文件服务

服务提供静态文件托管功能，支持图片、样式等资源访问：

**访问路径**：`/static/{file_path}`

**用途**：
- 配合 `image_mode=external` 模式存储转换后的图片
- 提供 Web 预览功能所需的静态资源

**代码位置**：[main.py](../app/main.py#L19)

#### 9.6.4 LLM 高级配置

多模态大模型客户端支持丰富的推理参数配置：

**基础参数**：
| 参数名 | 默认值 | 说明 |
|--------|--------|------|
| `LLM_BASE_URL` | `https://dhzh.dhccjy.com/openhub-model/v1` | 模型 API 地址 |
| `LLM_MODEL` | `Qwen3.6-Flash` | 模型名称 |
| `LLM_TEMPERATURE` | 0.7 | 温度参数（0-1） |
| `LLM_TOP_P` | 0.9 | Top-P 采样参数 |
| `LLM_MAX_TOKENS` | 16384 | 最大输出 Token 数 |
| `LLM_TIMEOUT` | 300 | 请求超时时间（秒） |

**高级参数**：
| 参数名 | 默认值 | 说明 |
|--------|--------|------|
| `LLM_STREAM` | true | 是否启用流式应答 |
| `LLM_INCLUDE_USAGE` | true | 是否在响应中包含 Token 用量 |
| `LLM_TOP_K` | null | 候选 Token 数量（非 OpenAI 标准参数） |
| `LLM_REPETITION_PENALTY` | null | 重复度惩罚系数 |
| `LLM_EXTRA_BODY` | `{}` | 额外参数（JSON 格式，传递至 extra_body） |
| `LLM_EXTRA_PARAMS` | `{}` | 额外顶层参数（JSON 格式） |

**流式响应特性**：
- 实时输出生成内容，降低感知延迟
- 自动收集 Token 用量统计信息
- 兼容多种网关的 reasoning_content 提取（支持 4 种获取方式）

**系统提示词**：
- `LLM_SYSTEM_PROMPT`：系统角色提示词，指导模型分析逻辑
- `LLM_PROMPT`：用户角色提示词，描述图片分析任务

**代码位置**：[llm_service.py](../app/services/llm_service.py)

### 9.7 版本历史

| 版本   | 日期         | 说明   |
| ---- | ---------- | ---- |
| V1.0 | 2026-05-15 | 初始版本 |

