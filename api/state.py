"""
Shared mutable state for the FastAPI application.

Route modules import from here to access the global components
(orchestrator, loader, etc.) that are initialized during the
application lifespan in main.py.
"""

from typing import Optional

from agent.orchestrator import Orchestrator
from etl.extract import DataExtractor
from etl.clean import DataCleaner
from etl.load import WarehouseLoader
from warehouse.schema import SchemaManager
from etl.watcher import DataWatcher

orchestrator: Optional[Orchestrator] = None
extractor: Optional[DataExtractor] = None
cleaner: Optional[DataCleaner] = None
loader: Optional[WarehouseLoader] = None
schema_manager: Optional[SchemaManager] = None
data_watcher: Optional[DataWatcher] = None
sql_tool = None
