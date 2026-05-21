import multiprocessing
from redis import Redis
from rq import Worker, Queue
from app.config import settings
from app.utils.logging_config import setup_logging
from loguru import logger


setup_logging(log_level=settings.LOG_LEVEL)


def _run_sync_worker(redis_host, redis_port, redis_db):
    try:
        redis_conn = Redis(host=redis_host, port=redis_port, db=redis_db)
        queues = [Queue(name="sync_conversion", connection=redis_conn)]
        worker = Worker(queues, connection=redis_conn)
        worker.work()
    except Exception as e:
        logger.error("[sync Worker] 异常退出: {}", e)


def _run_async_worker(redis_host, redis_port, redis_db):
    try:
        redis_conn = Redis(host=redis_host, port=redis_port, db=redis_db)
        queues = [Queue(name="async_conversion", connection=redis_conn)]
        worker = Worker(queues, connection=redis_conn)
        worker.work()
    except Exception as e:
        logger.error("[async Worker] 异常退出: {}", e)


if __name__ == "__main__":
    logger.info(
        "[Worker] 启动: sync={}, async={}",
        settings.SYNC_WORKER_COUNT,
        settings.ASYNC_WORKER_COUNT,
    )

    processes = []

    for i in range(settings.SYNC_WORKER_COUNT):
        p = multiprocessing.Process(
            target=_run_sync_worker,
            args=(settings.REDIS_HOST, settings.REDIS_PORT, settings.REDIS_DB),
            name=f"sync-worker-{i + 1}",
            daemon=True,
        )
        p.start()
        processes.append(p)
        logger.info("[Worker] sync-worker-{} 已启动 (pid={})", i + 1, p.pid)

    for i in range(settings.ASYNC_WORKER_COUNT):
        p = multiprocessing.Process(
            target=_run_async_worker,
            args=(settings.REDIS_HOST, settings.REDIS_PORT, settings.REDIS_DB),
            name=f"async-worker-{i + 1}",
            daemon=True,
        )
        p.start()
        processes.append(p)
        logger.info("[Worker] async-worker-{} 已启动 (pid={})", i + 1, p.pid)

    try:
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        logger.info("[Worker] 收到中断信号，正在停止所有 Worker...")
        for p in processes:
            p.terminate()
        for p in processes:
            p.join(timeout=5)
        logger.info("[Worker] 所有 Worker 已停止")
