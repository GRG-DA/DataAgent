"""
Live Data Monitor Module
Polling-based file watcher that detects new/changed data files and auto-imports them
"""

import logging
import time
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Set
from datetime import datetime
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class WatchedFile:
    """Represents a watched data file."""
    path: Path
    last_modified: float = 0
    last_size: int = 0
    status: str = "pending"  # pending, importing, done, error
    error: str = ""
    imported_at: str = ""
    row_count: int = 0


class DataWatcher:
    """Monitors directories for new/changed data files and auto-imports them.

    Uses polling to detect file changes (lightweight, no external deps).
    Can watch multiple directories for multiple file types.
    """

    def __init__(self, poll_interval: int = 30):
        self.poll_interval = poll_interval
        self._directories: List[Path] = []
        self._extensions: Set[str] = {".csv", ".xlsx", ".xls", ".parquet", ".json", ".jsonl", ".tsv"}
        self._watched_files: Dict[str, WatchedFile] = {}
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._import_callback: Optional[Callable] = None
        self._history: List[Dict] = []

    def set_import_callback(self, callback: Callable):
        """Set the function to call when a new file is detected.

        Callback receives: (file_path: str, table_name: str) -> dict with keys: success, rows, error
        """
        self._import_callback = callback

    def add_directory(self, directory: str):
        """Add a directory to watch."""
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        if path not in self._directories:
            self._directories.append(path)
            logger.info(f"Watching directory: {path}")

    def remove_directory(self, directory: str):
        """Remove a directory from watch."""
        path = Path(directory)
        if path in self._directories:
            self._directories.remove(path)

    def get_directories(self) -> List[str]:
        return [str(d) for d in self._directories]

    def set_extensions(self, extensions: List[str]):
        """Set file extensions to watch (e.g. ['.csv', '.xlsx'])."""
        self._extensions = set(ext.lower() for ext in extensions)

    def get_files(self) -> Dict[str, Any]:
        """Get status of all watched files."""
        return {name: {
            "path": str(f.path),
            "last_modified": f.last_modified,
            "size": f.last_size,
            "status": f.status,
            "error": f.error,
            "imported_at": f.imported_at,
            "row_count": f.row_count,
        } for name, f in self._watched_files.items()}

    def get_history(self) -> List[Dict]:
        """Get import history."""
        return self._history[-50:]  # Last 50 events

    def start(self):
        """Start the background watcher thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._watch_loop, daemon=True)
        self._thread.start()
        logger.info(f"Data watcher started (poll every {self.poll_interval}s)")

    def stop(self):
        """Stop the background watcher."""
        self._running = False
        logger.info("Data watcher stopped")

    def scan_now(self) -> List[Dict]:
        """Immediately scan directories for changes. Returns list of changes."""
        return self._scan_directories()

    def _watch_loop(self):
        """Main watching loop."""
        while self._running:
            try:
                self._scan_directories()
            except Exception as e:
                logger.error(f"Watch scan error: {e}")
            time.sleep(self.poll_interval)

    def _scan_directories(self) -> List[Dict]:
        """Scan all watched directories for file changes."""
        changes = []

        for directory in self._directories:
            if not directory.exists():
                continue

            for ext in self._extensions:
                for file_path in directory.glob(f"*{ext}"):
                    if not file_path.is_file():
                        continue

                    file_key = str(file_path)
                    mtime = file_path.stat().st_mtime
                    size = file_path.stat().st_size

                    # Check if new or modified
                    if file_key not in self._watched_files:
                        # New file
                        changes.append({"file": file_key, "change": "new"})
                        self._watched_files[file_key] = WatchedFile(
                            path=file_path, last_modified=mtime, last_size=size, status="pending",
                        )
                        self._auto_import(file_key)

                    else:
                        watched = self._watched_files[file_key]
                        if mtime > watched.last_modified or size != watched.last_size:
                            # Modified
                            changes.append({"file": file_key, "change": "modified"})
                            watched.last_modified = mtime
                            watched.last_size = size
                            watched.status = "pending"
                            self._auto_import(file_key)

        return changes

    def _auto_import(self, file_key: str):
        """Auto-import a detected file."""
        watched = self._watched_files.get(file_key)
        if not watched or not self._import_callback:
            return

        watched.status = "importing"
        table_name = watched.path.stem.replace(" ", "_").lower()

        try:
            result = self._import_callback(str(watched.path), table_name)
            if result.get("success"):
                watched.status = "done"
                watched.row_count = result.get("rows", 0)
                watched.imported_at = datetime.now().isoformat()
                logger.info(f"Auto-imported {file_key} -> {table_name} ({result.get('rows', 0)} rows)")
            else:
                watched.status = "error"
                watched.error = result.get("error", "Unknown error")
                logger.warning(f"Auto-import failed for {file_key}: {watched.error}")

            self._history.append({
                "file": file_key,
                "table": table_name,
                "timestamp": watched.imported_at or datetime.now().isoformat(),
                "status": watched.status,
                "rows": watched.row_count,
                "error": watched.error,
            })

        except Exception as e:
            watched.status = "error"
            watched.error = str(e)
            logger.error(f"Auto-import exception for {file_key}: {e}")
