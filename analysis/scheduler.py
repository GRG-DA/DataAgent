"""
Simple Task Scheduler for automated report generation.
Runs in a background thread and generates reports on schedule.
"""
import logging
import time
import threading
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any

logger = logging.getLogger(__name__)

# Schedule configuration file
SCHEDULE_FILE = Path("data/schedules.json")


class ScheduledReport:
    """A single scheduled report configuration."""

    def __init__(
        self,
        name: str,
        table_name: str,
        schedule: str,  # "daily", "weekly", "hourly"
        recipients: List[str] = None,
        enabled: bool = True,
        include_charts: bool = False,
        last_run: str = None,
    ):
        self.name = name
        self.table_name = table_name
        self.schedule = schedule
        self.recipients = recipients or []
        self.enabled = enabled
        self.include_charts = include_charts
        self.last_run = last_run
        self.created_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "table_name": self.table_name,
            "schedule": self.schedule,
            "recipients": self.recipients,
            "enabled": self.enabled,
            "include_charts": self.include_charts,
            "last_run": self.last_run,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ScheduledReport":
        r = cls(
            name=data["name"],
            table_name=data["table_name"],
            schedule=data["schedule"],
            recipients=data.get("recipients", []),
            enabled=data.get("enabled", True),
            include_charts=data.get("include_charts", False),
            last_run=data.get("last_run"),
        )
        r.created_at = data.get("created_at", r.created_at)
        return r

    def should_run(self) -> bool:
        """Check if this schedule should run now."""
        if not self.enabled:
            return False
        if not self.last_run:
            return True

        last = datetime.fromisoformat(self.last_run)
        now = datetime.now()

        if self.schedule == "hourly":
            return (now - last).total_seconds() >= 3600
        elif self.schedule == "daily":
            return (now - last).days >= 1
        elif self.schedule == "weekly":
            return (now - last).days >= 7
        return False

    def mark_run(self):
        """Mark this schedule as having run now."""
        self.last_run = datetime.now().isoformat()


class ReportScheduler:
    """Background scheduler that generates reports automatically."""

    def __init__(self, output_dir: str = "outputs/reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._schedules: Dict[str, ScheduledReport] = {}
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._report_generator = None
        self._load_schedules()

    def set_report_generator(self, generator_func: Callable):
        """Set the function to call for report generation."""
        self._report_generator = generator_func

    def add_schedule(self, report: ScheduledReport) -> bool:
        if report.name in self._schedules:
            return False
        self._schedules[report.name] = report
        self._save_schedules()
        return True

    def remove_schedule(self, name: str) -> bool:
        if name not in self._schedules:
            return False
        del self._schedules[name]
        self._save_schedules()
        return True

    def get_schedules(self) -> List[dict]:
        return [s.to_dict() for s in self._schedules.values()]

    def update_schedule(self, name: str, updates: dict) -> bool:
        if name not in self._schedules:
            return False
        s = self._schedules[name]
        for k, v in updates.items():
            if hasattr(s, k):
                setattr(s, k, v)
        self._save_schedules()
        return True

    def start(self):
        """Start the background scheduler thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        logger.info("Report scheduler started")

    def stop(self):
        """Stop the background scheduler."""
        self._running = False
        logger.info("Report scheduler stopped")

    def _run_loop(self):
        """Main scheduler loop - runs every 60 seconds."""
        while self._running:
            for name, schedule in list(self._schedules.items()):
                if schedule.should_run() and self._report_generator:
                    try:
                        logger.info(f"Generating scheduled report: {name}")
                        result = self._report_generator(schedule.table_name, schedule.include_charts)
                        schedule.mark_run()
                        self._save_schedules()
                        logger.info(f"Report '{name}' generated successfully")
                    except Exception as e:
                        logger.error(f"Failed to generate report '{name}': {e}")
            time.sleep(60)

    def _load_schedules(self):
        """Load schedules from JSON file."""
        try:
            if SCHEDULE_FILE.exists():
                with open(SCHEDULE_FILE) as f:
                    data = json.load(f)
                    for item in data:
                        r = ScheduledReport.from_dict(item)
                        self._schedules[r.name] = r
                logger.info(f"Loaded {len(self._schedules)} schedules")
        except Exception as e:
            logger.warning(f"Failed to load schedules: {e}")

    def _save_schedules(self):
        """Save schedules to JSON file."""
        try:
            SCHEDULE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = [s.to_dict() for s in self._schedules.values()]
            with open(SCHEDULE_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save schedules: {e}")
