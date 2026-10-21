"""
Analysis Package
Statistical analysis, data quality, insights, and reporting for the data agent
"""

from .statistical import (
    test_normality,
    test_hypothesis,
    is_error_result,
    decompose_time_series,
    fit_regression,
    compute_feature_importance,
    compute_confidence_interval,
    run_ab_test,
    compute_power_analysis,
    test_conversion_rate,
    sequential_test,
)
from .quality import compute_quality_score, detect_pii, generate_quality_report
from .insights import generate_auto_insights, generate_narrative, generate_llm_insights
from .recommender import recommend_charts
from .timeseries import advanced_decomposition, enhanced_forecast
from .forecasting import advanced_forecast, generate_whatif_scenarios, ForecastResult
from .cohort import cohort_retention, churn_analysis, CohortResult, ChurnResult
from .comparison import compare_periods, diff_tables, ComparisonResult, TableDiffResult
from .report import ReportGenerator, ReportSection, GeneratedReport
from .cache import AnalysisCache, cache, cached, invalidate_cache_for
from .scheduler import ScheduledReport, ReportScheduler, SCHEDULE_FILE
from .lineage import LineageTracker
from .bi_export import (
    export_to_tde,
    export_to_powerbi,
    export_to_looker,
    generate_connection_config,
)

__all__ = [
    # Statistical
    "test_normality",
    "test_hypothesis",
    "decompose_time_series",
    "fit_regression",
    "compute_feature_importance",
    "compute_confidence_interval",
    "run_ab_test",
    "compute_power_analysis",
    # Quality
    "compute_quality_score",
    "detect_pii",
    "generate_quality_report",
    # Insights
    "generate_auto_insights",
    "generate_narrative",
    # Recommendations
    "recommend_charts",
    # Time Series
    "advanced_decomposition",
    "enhanced_forecast",
    # Report
    "ReportGenerator",
    "ReportSection",
    "GeneratedReport",
    # Cache
    "AnalysisCache",
    "cache",
    "cached",
    "invalidate_cache_for",
    # Scheduler
    "ScheduledReport",
    "ReportScheduler",
    "SCHEDULE_FILE",
    # Lineage
    "LineageTracker",
    # BI Export
    "export_to_tde",
    "export_to_powerbi",
    "export_to_looker",
    "generate_connection_config",
]
