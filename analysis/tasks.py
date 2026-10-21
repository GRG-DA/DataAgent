"""
Background Task Execution Module
Manages long-running queries and analysis tasks with status tracking,
progress reporting, and cancellation support.
"""

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class BackgroundTask:
    """A tracked background task."""
    task_id: str
    name: str
    status: TaskStatus = TaskStatus.PENDING
    progress: float = 0.0  # 0-100
    message: str = ""
    result: Any = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    cancel_event: Optional[threading.Event] = field(default=None, repr=False)


class TaskManager:
    """Manages background tasks with threading."""

    def __init__(self, max_concurrent: int = 4):
        self._tasks: Dict[str, BackgroundTask] = {}
        self._lock = threading.Lock()
        self._max_concurrent = max_concurrent
        self._active_count = 0

    def submit(
        self,
        name: str,
        func: Callable,
        args: tuple = (),
        kwargs: Optional[Dict] = None,
    ) -> str:
        """Submit a function to run in a background thread.

        Returns a task_id for tracking.
        """
        task_id = str(uuid.uuid4())[:12]
        cancel_event = threading.Event()

        task = BackgroundTask(
            task_id=task_id,
            name=name,
            cancel_event=cancel_event,
        )
        with self._lock:
            self._tasks[task_id] = task

        def _run():
            with self._lock:
                self._active_count += 1
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
            try:
                result = func(*args, **(kwargs or {}))
                if cancel_event.is_set():
                    task.status = TaskStatus.CANCELLED
                    task.message = "Cancelled by user"
                else:
                    task.result = result
                    task.status = TaskStatus.COMPLETED
                    task.progress = 100.0
                    task.message = "Completed"
            except Exception as e:
                task.status = TaskStatus.FAILED
                task.error = str(e)
                task.message = f"Failed: {e}"
                logger.error(f"Background task {task_id} failed: {e}")
            finally:
                task.completed_at = time.time()
                with self._lock:
                    self._active_count -= 1

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        return task_id

    def get_task(self, task_id: str) -> Optional[BackgroundTask]:
        """Get task status."""
        return self._tasks.get(task_id)

    def get_all_tasks(self) -> List[Dict[str, Any]]:
        """Get all tasks as serializable dicts."""
        with self._lock:
            tasks = list(self._tasks.values())
        return [
            {
                "task_id": t.task_id,
                "name": t.name,
                "status": t.status.value,
                "progress": t.progress,
                "message": t.message,
                "error": t.error,
                "created_at": t.created_at,
                "started_at": t.started_at,
                "completed_at": t.completed_at,
                "duration": round(t.completed_at - t.started_at, 2) if t.completed_at and t.started_at else None,
            }
            for t in tasks
        ]

    def cancel(self, task_id: str) -> bool:
        """Request cancellation of a running task."""
        task = self._tasks.get(task_id)
        if task and task.cancel_event:
            task.cancel_event.set()
            return True
        return False

    def update_progress(self, task_id: str, progress: float, message: str = ""):
        """Update task progress (called from within the task function)."""
        task = self._tasks.get(task_id)
        if task:
            task.progress = min(100.0, max(0.0, progress))
            if message:
                task.message = message

    def cleanup_old(self, max_age_seconds: int = 3600):
        """Remove completed tasks older than max_age_seconds."""
        now = time.time()
        to_remove = []
        with self._lock:
            for tid, task in self._tasks.items():
                if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                    if task.completed_at and now - task.completed_at > max_age_seconds:
                        to_remove.append(tid)
            for tid in to_remove:
                del self._tasks[tid]

    @property
    def active_count(self) -> int:
        return self._active_count

    @property
    def pending_count(self) -> int:
        return sum(1 for t in self._tasks.values() if t.status == TaskStatus.PENDING)


# Global task manager
task_manager = TaskManager()
