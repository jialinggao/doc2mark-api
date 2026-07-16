import time
import math
import psutil
from collections import defaultdict
from datetime import datetime, date
from threading import Lock


class MetricsCollector:
    def __init__(self):
        self._lock = Lock()
        self.total_requests = 0
        self.total_errors = 0
        self.daily_requests = defaultdict(int)
        self.daily_errors = defaultdict(int)
        self.response_times = []
        self.daily_response_times = defaultdict(list)
        self.max_response_times = 10000
        
        self.task_type_metrics = {
            "text_only": [],
            "with_ocr": [],
            "with_llm": [],
            "ocr_plus_llm": []
        }
    
    def _get_task_type(self, enable_ocr: bool = False, enable_llm: bool = False) -> str:
        if enable_ocr and enable_llm:
            return "ocr_plus_llm"
        elif enable_ocr:
            return "with_ocr"
        elif enable_llm:
            return "with_llm"
        else:
            return "text_only"
    
    def record_request(self, duration_ms: float, is_error: bool = False, enable_ocr: bool = False, enable_llm: bool = False):
        with self._lock:
            self.total_requests += 1
            today = date.today().isoformat()
            self.daily_requests[today] += 1
            
            self.response_times.append(duration_ms)
            self.daily_response_times[today].append(duration_ms)
            
            if len(self.response_times) > self.max_response_times:
                self.response_times = self.response_times[-self.max_response_times:]
            if len(self.daily_response_times[today]) > self.max_response_times:
                self.daily_response_times[today] = self.daily_response_times[today][-self.max_response_times:]
            
            if is_error:
                self.total_errors += 1
                self.daily_errors[today] += 1
            
            task_type = self._get_task_type(enable_ocr, enable_llm)
            self.task_type_metrics[task_type].append(duration_ms)
            if len(self.task_type_metrics[task_type]) > self.max_response_times:
                self.task_type_metrics[task_type] = self.task_type_metrics[task_type][-self.max_response_times:]
    
    def post_request_record(self, task_type: str, duration_ms: float):
        with self._lock:
            self.task_type_metrics[task_type].append(duration_ms)
            if len(self.task_type_metrics[task_type]) > self.max_response_times:
                self.task_type_metrics[task_type] = self.task_type_metrics[task_type][-self.max_response_times:]
    
    def get_percentile(self, data: list, percentile: float) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(data)
        index = math.ceil(percentile / 100.0 * len(sorted_data)) - 1
        index = max(0, min(index, len(sorted_data) - 1))
        return sorted_data[index]
    
    def get_metrics(self) -> dict:
        with self._lock:
            today = date.today().isoformat()
            today_requests = self.daily_requests.get(today, 0)
            today_errors = self.daily_errors.get(today, 0)
            today_times = self.daily_response_times.get(today, [])
            
            success_rate = ((self.total_requests - self.total_errors) / self.total_requests * 100) if self.total_requests > 0 else 100.0
            
            all_p50 = self.get_percentile(self.response_times, 50)
            all_p95 = self.get_percentile(self.response_times, 95)
            all_p99 = self.get_percentile(self.response_times, 99)
            
            today_avg = sum(today_times) / len(today_times) if today_times else 0
            
            task_type_stats = {}
            for task_type, times in self.task_type_metrics.items():
                if times:
                    task_type_stats[task_type] = {
                        "count": len(times),
                        "avg_ms": round(sum(times) / len(times), 2),
                        "p50_ms": round(self.get_percentile(times, 50), 2),
                        "p95_ms": round(self.get_percentile(times, 95), 2),
                        "p99_ms": round(self.get_percentile(times, 99), 2)
                    }
            
            return {
                "requests": {
                    "total": self.total_requests,
                    "today": today_requests,
                    "success_rate": round(success_rate, 2)
                },
                "performance": {
                    "avg_response_time_ms": round(sum(self.response_times) / len(self.response_times), 2) if self.response_times else 0,
                    "today_avg_ms": round(today_avg, 2),
                    "p50_ms": round(all_p50, 2),
                    "p95_ms": round(all_p95, 2),
                    "p99_ms": round(all_p99, 2)
                },
                "task_type_performance": task_type_stats
            }
    
    @staticmethod
    def get_resource_usage() -> dict:
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        return {
            "cpu_usage": round(cpu_percent, 2),
            "memory_usage": round(memory.percent, 2),
            "disk_usage": round(disk.percent, 2),
            "memory_available_mb": round(memory.available / 1024 / 1024, 2),
            "disk_free_gb": round(disk.free / 1024 / 1024 / 1024, 2)
        }


metrics_collector = MetricsCollector()