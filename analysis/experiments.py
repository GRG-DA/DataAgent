"""
Experiment Registry for A/B Tests
Tracks experiments with start/end dates, status, multi-metric support,
and experiment history.
"""

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

EXPERIMENTS_DIR = "data/experiments"
EXPERIMENTS_FILE = os.path.join(EXPERIMENTS_DIR, "experiments.json")


@dataclass
class Experiment:
    """An A/B test experiment."""
    name: str
    description: str = ""
    table_name: str = ""
    group_col: str = ""
    metric_cols: List[str] = field(default_factory=list)
    group_a: str = ""
    group_b: str = ""
    status: str = "draft"  # draft, running, completed, stopped
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    hypothesis: str = ""
    primary_metric: str = ""
    alpha: float = 0.05
    target_power: float = 0.80
    results: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)
    owner: str = ""


class ExperimentRegistry:
    """Manages A/B test experiments."""

    def __init__(self, experiments_dir: str = EXPERIMENTS_DIR):
        self._dir = experiments_dir
        self._experiments: List[Experiment] = []
        os.makedirs(self._dir, exist_ok=True)
        self._load()

    def _load(self):
        if os.path.isfile(EXPERIMENTS_FILE):
            try:
                with open(EXPERIMENTS_FILE, "r") as f:
                    data = json.load(f)
                self._experiments = [Experiment(**e) for e in data]
            except Exception:
                self._experiments = []
        else:
            self._experiments = []

    def _save(self):
        with open(EXPERIMENTS_FILE, "w") as f:
            json.dump([asdict(e) for e in self._experiments], f, indent=2)

    def create(self, experiment: Experiment) -> str:
        """Create a new experiment."""
        # Check for duplicate name
        if any(e.name == experiment.name for e in self._experiments):
            raise ValueError(f"Experiment '{experiment.name}' already exists")
        self._experiments.append(experiment)
        self._save()
        return experiment.name

    def get(self, name: str) -> Optional[Experiment]:
        """Get an experiment by name."""
        for e in self._experiments:
            if e.name == name:
                return e
        return None

    def list_all(self, status: Optional[str] = None) -> List[Experiment]:
        """List all experiments, optionally filtered by status."""
        if status:
            return [e for e in self._experiments if e.status == status]
        return list(self._experiments)

    def update(self, name: str, **kwargs) -> bool:
        """Update an experiment's fields."""
        exp = self.get(name)
        if not exp:
            return False
        for key, value in kwargs.items():
            if hasattr(exp, key):
                setattr(exp, key, value)
        self._save()
        return True

    def start(self, name: str) -> bool:
        """Mark an experiment as running."""
        return self.update(name, status="running", start_date=datetime.now(timezone.utc).isoformat())

    def complete(self, name: str, results: Optional[Dict] = None) -> bool:
        """Mark an experiment as completed."""
        updates = {"status": "completed", "end_date": datetime.now(timezone.utc).isoformat()}
        if results:
            updates["results"] = results
        return self.update(name, **updates)

    def stop(self, name: str) -> bool:
        """Stop an experiment."""
        return self.update(name, status="stopped", end_date=datetime.now(timezone.utc).isoformat())

    def delete(self, name: str) -> bool:
        """Delete an experiment."""
        original = len(self._experiments)
        self._experiments = [e for e in self._experiments if e.name != name]
        if len(self._experiments) < original:
            self._save()
            return True
        return False

    def get_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get experiment history, sorted by start date."""
        experiments = sorted(
            self._experiments,
            key=lambda e: e.start_date or e.end_date or "",
            reverse=True,
        )
        return [asdict(e) for e in experiments[:limit]]

    def get_stats(self) -> Dict[str, Any]:
        """Get experiment statistics."""
        total = len(self._experiments)
        by_status = {}
        for e in self._experiments:
            by_status[e.status] = by_status.get(e.status, 0) + 1
        return {
            "total": total,
            "by_status": by_status,
            "running": by_status.get("running", 0),
            "completed": by_status.get("completed", 0),
        }
