"""
Worker 进程管理器

启动流程：
1. 启动 OCR 引擎进程（独立进程，持有 PaddleOCR 实例）
2. 启动 Structure 引擎进程（独立进程，持有 PP-StructureV3 实例）
3. 启动 Worker 进程（通过 IPC 共享引擎资源）
4. 监控引擎进程状态，崩溃时自动重启
"""
import time
import multiprocessing
from redis import Redis
from rq import SimpleWorker, Queue
from app.config import settings
from app.utils.logging_config import setup_logging
from loguru import logger


setup_logging(log_level=settings.LOG_LEVEL)

BANNER = r"""

▄▄▄▄▄▄               ▄▄▄▄▄▄▄  ▄▄▄      ▄▄▄                      ▄▄▄▄   ▄▄▄▄▄▄▄   ▄▄▄▄▄ 
███▀▀██▄             ▀▀▀▀████ ████▄  ▄████             ▄▄     ▄██▀▀██▄ ███▀▀███▄  ███  
███  ███ ▄███▄ ▄████    ▄██▀  ███▀████▀███  ▀▀█▄ ████▄ ██ ▄█▀ ███  ███ ███▄▄███▀  ███  
███  ███ ██ ██ ██     ▄███▄▄▄ ███  ▀▀  ███ ▄█▀██ ██ ▀▀ ████   ███▀▀███ ███▀▀▀▀    ███  
██████▀  ▀███▀ ▀████ ████████ ███      ███ ▀█▄██ ██    ██ ▀█▄ ███  ███ ███       ▄███▄ 

---------------------------------------------------------------------------------------

                                         ▌        
                               ▌  ▌▞▀▖▙▀▖▌▗▘▞▀▖▙▀▖
                               ▐▐▐ ▌ ▌▌  ▛▚ ▛▀ ▌  
                                ▘▘ ▝▀ ▘  ▘ ▘▝▀▘▘  

  worker 服务主要功能:
    - 后台消费 Redis 任务队列，执行文档转换任务
    - 启动并管理 OCR 引擎进程（PaddleOCR）和 Structure 引擎进程（PP-StructureV3）
    - 引擎进程崩溃时自动重启，保证服务可用性
    - 预加载转换器及 LLM 资源，提升任务处理速度
"""

# Unix Socket 路径
OCR_SOCKET_PATH = '/tmp/ocr.sock'
STRUCTURE_SOCKET_PATH = '/tmp/structure.sock'


# ─── 引擎进程 ─────────────────────────────────────────────────

def _run_ocr_engine():
    """启动 OCR 引擎进程"""
    try:
        from app.services.ocr_engine import run_ocr_engine
        run_ocr_engine(OCR_SOCKET_PATH)
    except Exception as e:
        logger.error("[OCREngine] 进程异常退出: {}", e)


def _run_structure_engine():
    """启动 Structure 引擎进程"""
    try:
        from app.services.structure_engine import run_structure_engine
        run_structure_engine(STRUCTURE_SOCKET_PATH)
    except Exception as e:
        logger.error("[StructureEngine] 进程异常退出: {}", e)


# ─── Worker 预加载 ─────────────────────────────────────────────

def _preload_worker_resources():
    """预加载 Worker 进程需要的资源（不含引擎资源）"""
    logger.info("[Preload] 开始预加载 Worker 资源...")

    # 1. 预加载转换器模块（触发所有模块导入）
    from app.services.converter import converter_service  # noqa: F811

    # 2. 预加载 MarkItDown 实例（WordConverter / GeneralConverter）
    try:
        from app.services.word_converter import word_converter
        _ = word_converter.md  # 触发延迟初始化
        from app.services.general_converter import general_converter
        _ = general_converter.md  # 触发延迟初始化
        logger.info("[Preload] MarkItDown 预加载完成")
    except Exception as e:
        logger.warning("[Preload] MarkItDown 预加载失败: {}", e)

    # 3. 预加载 LLM 客户端
    if settings.ENABLE_LLM:
        from app.services.llm_service import llm_service
        _ = llm_service.client  # 触发延迟初始化
        logger.info("[Preload] LLM 资源预加载完成")

    # 4. 设置 OCR 引擎 Socket 路径
    try:
        from app.services.ocr_service import ocr_service
        ocr_service.socket_path = OCR_SOCKET_PATH
        logger.info("[Preload] OCR 引擎已配置: {}", OCR_SOCKET_PATH)
    except Exception as e:
        logger.warning("[Preload] OCR 引擎配置失败: {}", e)

    # 5. 设置 Structure 引擎 Socket 路径
    try:
        from app.services.pdf_structure_converter import pdf_structure_converter
        pdf_structure_converter.mode = 'remote'
        pdf_structure_converter.socket_path = STRUCTURE_SOCKET_PATH
        logger.info("[Preload] PPStructure 引擎已配置: {}", STRUCTURE_SOCKET_PATH)
    except Exception as e:
        logger.warning("[Preload] PPStructure 引擎配置失败: {}", e)

    logger.info("[Preload] Worker 资源预加载完成")


# ─── Worker 进程 ───────────────────────────────────────────────

def _run_sync_worker(redis_host, redis_port, redis_db):
    try:
        _preload_worker_resources()
        redis_conn = Redis(host=redis_host, port=redis_port, db=redis_db)
        queues = [Queue(name="sync_conversion", connection=redis_conn)]
        worker = SimpleWorker(queues, connection=redis_conn)
        worker.work()
    except Exception as e:
        logger.error("[sync Worker] 异常退出: {}", e)


def _run_async_worker(redis_host, redis_port, redis_db):
    try:
        _preload_worker_resources()
        redis_conn = Redis(host=redis_host, port=redis_port, db=redis_db)
        queues = [Queue(name="async_conversion", connection=redis_conn)]
        worker = SimpleWorker(queues, connection=redis_conn)
        worker.work()
    except Exception as e:
        logger.error("[async Worker] 异常退出: {}", e)


# ─── 引擎监控 ──────────────────────────────────────────────────

def _monitor_engines(engine_processes: dict, check_interval: int = 10):
    """
    监控引擎进程状态，崩溃时自动重启

    Args:
        engine_processes: {name: Process} 字典
        check_interval: 检查间隔（秒）
    """
    while True:
        time.sleep(check_interval)
        for name, proc in list(engine_processes.items()):
            if not proc.is_alive():
                exitcode = proc.exitcode if proc.exitcode is not None else -1
                logger.warning("[Monitor] {} 进程异常退出 (exitcode={}), 正在重启...", name, exitcode)

                if name == 'ocr_engine':
                    new_proc = multiprocessing.Process(
                        target=_run_ocr_engine,
                        name='ocr_engine',
                        daemon=True,
                    )
                elif name == 'structure_engine':
                    new_proc = multiprocessing.Process(
                        target=_run_structure_engine,
                        name='structure_engine',
                        daemon=True,
                    )
                else:
                    continue

                new_proc.start()
                engine_processes[name] = new_proc
                logger.info("[Monitor] {} 已重启 (pid={})", name, new_proc.pid)


# ─── 主入口 ────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("\n{}", BANNER)
    logger.info("[Worker] 启动: sync={}, async={}",
        settings.SYNC_WORKER_COUNT,
        settings.ASYNC_WORKER_COUNT,
    )

    # 1. 启动引擎进程
    engine_processes = {}

    if settings.OCR_ENABLED:
        ocr_proc = multiprocessing.Process(
            target=_run_ocr_engine,
            name='ocr_engine',
            daemon=True,
        )
        ocr_proc.start()
        engine_processes['ocr_engine'] = ocr_proc
        logger.info("[Engine] OCR 引擎已启动 (pid={})", ocr_proc.pid)

    structure_proc = multiprocessing.Process(
        target=_run_structure_engine,
        name='structure_engine',
        daemon=True,
    )
    structure_proc.start()
    engine_processes['structure_engine'] = structure_proc
    logger.info("[Engine] Structure 引擎已启动 (pid={})", structure_proc.pid)

    # 等待引擎进程启动完成（socket 就绪）
    if engine_processes:
        logger.info("[Engine] 等待引擎进程启动...")
        time.sleep(3)

    # 2. 启动引擎监控线程
    import threading
    monitor_thread = threading.Thread(
        target=_monitor_engines,
        args=(engine_processes,),
        daemon=True,
        name='engine-monitor',
    )
    monitor_thread.start()
    logger.info("[Monitor] 引擎监控线程已启动")

    # 3. 启动 Worker 进程
    worker_processes = []

    for i in range(settings.SYNC_WORKER_COUNT):
        p = multiprocessing.Process(
            target=_run_sync_worker,
            args=(settings.REDIS_HOST, settings.REDIS_PORT, settings.REDIS_DB),
            name=f"sync-worker-{i + 1}",
            daemon=True,
        )
        p.start()
        worker_processes.append(p)
        logger.info("[Worker] sync-worker-{} 已启动 (pid={})", i + 1, p.pid)

    for i in range(settings.ASYNC_WORKER_COUNT):
        p = multiprocessing.Process(
            target=_run_async_worker,
            args=(settings.REDIS_HOST, settings.REDIS_PORT, settings.REDIS_DB),
            name=f"async-worker-{i + 1}",
            daemon=True,
        )
        p.start()
        worker_processes.append(p)
        logger.info("[Worker] async-worker-{} 已启动 (pid={})", i + 1, p.pid)

    # 4. 等待所有进程
    all_processes = worker_processes + list(engine_processes.values())
    try:
        while True:
            # 检查是否有进程全部退出
            alive = [p for p in all_processes if p.is_alive()]
            if not alive:
                logger.info("[Worker] 所有进程已退出")
                break
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("[Worker] 收到中断信号，正在停止所有进程...")
        for p in all_processes:
            try:
                p.terminate()
            except Exception:
                pass
        for p in all_processes:
            p.join(timeout=5)
        logger.info("[Worker] 所有进程已停止")