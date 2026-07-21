import os
import time
import asyncio
from datetime import datetime, timedelta
from loguru import logger
from app.config import settings


async def cleanup_expired_jobs():
    try:
        from redis import Redis
        from rq import Queue
        from rq.job import Job

        redis_conn = Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
        )
        sync_queue = Queue(name="sync_conversion", connection=redis_conn)
        async_queue = Queue(name="async_conversion", connection=redis_conn)

        cutoff = datetime.utcnow() - timedelta(seconds=settings.TASK_TTL)
        cleaned = 0

        for queue in [sync_queue, async_queue]:
            for registry in [queue.finished_job_registry, queue.failed_job_registry]:
                job_ids = registry.get_job_ids()
                for job_id in job_ids:
                    try:
                        job = Job.fetch(job_id, connection=redis_conn)
                        ended_at = job.ended_at
                        if ended_at and ended_at < cutoff:
                            job.delete(remove_from_queue=True)
                            cleaned += 1
                    except Exception:
                        pass

        if cleaned > 0:
            logger.info("[Cleanup] 清理过期任务 {} 个", cleaned)
    except Exception as e:
        logger.warning("[Cleanup] 清理过期任务失败: {}", e)


async def cleanup_loop():
    interval = settings.CLEANUP_INTERVAL_MINUTES * 60
    logger.info("[Cleanup] 定时清理任务启动，间隔 {} 分钟", settings.CLEANUP_INTERVAL_MINUTES)
    while True:
        await asyncio.sleep(interval)
        await cleanup_expired_jobs()


def start_cleanup_task(app):
    @app.on_event("startup")
    async def _startup():
        asyncio.create_task(cleanup_loop())
