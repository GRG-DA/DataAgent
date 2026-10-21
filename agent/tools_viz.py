"""
Visualization Tool Module
Create charts and visualizations using Plotly and Matplotlib
"""

import logging
from typing import Dict, List, Optional, Any, Union
from pathlib import Path
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio
import matplotlib

# Headless Agg backend, set BEFORE pyplot import: charts are rendered to
# files/buffers only (never shown interactively), and the frozen .exe ships
# without tkinter — pyplot would otherwise pick TkAgg on machines with a
# display and crash on `import tkinter` at first render.
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
from dataclasses import dataclass
import json
import base64
from io import BytesIO

logger = logging.getLogger(__name__)


@dataclass
class VizResult:
    """Result of visualization creation."""
    success: bool
    figure: Any = None  # Plotly figure or matplotlib figure
    html: str = ""
    error: str = ""
    metadata: Dict[str, Any] = None
    config: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.config is None:
            self.config = {}


class VizTool:
    """
    Visualization tool for creating charts.

    Supports:
    - Plotly (interactive, web-ready)
    - Matplotlib/Seaborn (static, publication-ready)
    - Auto chart type detection
    - Export to HTML, PNG, JSON
    """

    def __init__(
        self,
        default_engine: str = "plotly",
        default_theme: str = "plotly_white",
        max_points: int = 10000,
        output_dir: str = "outputs/charts",
    ):
        self.default_engine = default_engine
        self.default_theme = default_theme
        self.max_points = max_points
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Set default template
        pio.templates.default = default_theme
        plt.style.use('seaborn-v0_8-whitegrid')
        sns.set_palette("husl")

    def create_chart(
        self,
        df: pd.DataFrame,
        chart_type: str,
        **kwargs,
    ) -> VizResult:
        """
        Create a chart from DataFrame.

        Args:
            df: Input DataFrame
            chart_type: Type of chart (bar, line, scatter, histogram, box, heatmap, pie, area, violin)
            **kwargs: Chart-specific parameters

        Returns:
            VizResult with figure and metadata
        """
        if len(df) > self.max_points:
            df = df.sample(n=self.max_points, random_state=42)
            logger.warning(f"Data sampled to {self.max_points} points")

        chart_type = chart_type.lower()

        try:
            if self.default_engine == "plotly":
                fig = self._create_plotly_chart(df, chart_type, **kwargs)
                html = fig.to_html(include_plotlyjs="cdn", full_html=False)
            else:
                fig = self._create_matplotlib_chart(df, chart_type, **kwargs)
                html = self._mpl_to_html(fig)

            return VizResult(
                success=True,
                figure=fig,
                html=html,
                metadata={
                    "chart_type": chart_type,
                    "rows": len(df),
                    "columns": list(df.columns),
                    "engine": self.default_engine,
                },
                config=kwargs,
            )
        except Exception as e:
            logger.error(f"Chart creation failed: {e}")
            return VizResult(success=False, error=str(e), config=kwargs)

    # ============================================================
    # Plotly Charts
    # ============================================================

    def _create_plotly_chart(
        self,
        df: pd.DataFrame,
        chart_type: str,
        **kwargs,
    ) -> go.Figure:
        """Create Plotly chart based on type."""

        x = kwargs.get("x")
        y = kwargs.get("y")
        color = kwargs.get("color")
        facet_col = kwargs.get("facet_col")
        facet_row = kwargs.get("facet_row")
        title = kwargs.get("title", "")
        template = kwargs.get("template", self.default_theme)
        height = kwargs.get("height", 500)
        width = kwargs.get("width", None)

        if chart_type == "bar":
            fig = px.bar(
                df, x=x, y=y, color=color,
                facet_col=facet_col, facet_row=facet_row,
                title=title, template=template,
                barmode=kwargs.get("barmode", "group"),
                text_auto=kwargs.get("text_auto", False),
            )
        elif chart_type == "line":
            fig = px.line(
                df, x=x, y=y, color=color,
                facet_col=facet_col, facet_row=facet_row,
                title=title, template=template,
                markers=kwargs.get("markers", True),
            )
        elif chart_type == "scatter":
            trendline = kwargs.get("trendline")
            if trendline and x and x in df.columns and not pd.api.types.is_numeric_dtype(df[x]):
                # OLS (statsmodels) cannot fit a trend over categorical x
                # ("ufunc 'isnan' not supported"); drop the trendline rather
                # than failing the whole chart.
                trendline = None
            fig = px.scatter(
                df, x=x, y=y, color=color,
                facet_col=facet_col, facet_row=facet_row,
                title=title, template=template,
                size=kwargs.get("size"),
                hover_data=kwargs.get("hover_data"),
                trendline=trendline,
            )
        elif chart_type == "histogram":
            fig = px.histogram(
                df, x=x, y=y, color=color,
                facet_col=facet_col, facet_row=facet_row,
                title=title, template=template,
                nbins=kwargs.get("nbins", 30),
                marginal=kwargs.get("marginal", "box"),
            )
        elif chart_type == "box":
            fig = px.box(
                df, x=x, y=y, color=color,
                facet_col=facet_col, facet_row=facet_row,
                title=title, template=template,
                points=kwargs.get("points", "outliers"),
            )
        elif chart_type == "violin":
            fig = px.violin(
                df, x=x, y=y, color=color,
                facet_col=facet_col, facet_row=facet_row,
                title=title, template=template,
                box=kwargs.get("box", True),
                points=kwargs.get("points", "outliers"),
            )
        elif chart_type == "heatmap":
            # For heatmap, expect pivot table or correlation matrix
            z = kwargs.get("z")  # column for values
            if z and z in df.columns:
                # Pivot if needed
                if x and y:
                    pivot = df.pivot_table(values=z, index=y, columns=x, aggfunc="mean")
                else:
                    pivot = df.select_dtypes(include="number").corr()
            else:
                pivot = df.select_dtypes(include="number").corr()

            fig = px.imshow(
                pivot,
                title=title,
                template=template,
                color_continuous_scale=kwargs.get("colorscale", "RdBu_r"),
                aspect=kwargs.get("aspect", "auto"),
            )
        elif chart_type == "pie":
            # A single category makes a full-circle pie: zero information and
            # a confusing legend. Degrade to a bar so the value is still shown.
            if x and x in df.columns and df[x].nunique(dropna=True) < 2:
                fig = px.bar(
                    df, x=x, y=y, color=color,
                    title=title, template=template,
                )
            else:
                fig = px.pie(
                    df, names=x, values=y, color=color,
                    title=title, template=template,
                    hole=kwargs.get("hole", 0),
                )
        elif chart_type == "area":
            fig = px.area(
                df, x=x, y=y, color=color,
                facet_col=facet_col, facet_row=facet_row,
                title=title, template=template,
                groupnorm=kwargs.get("groupnorm"),
            )
        elif chart_type == "sunburst":
            path = kwargs.get("path", [x] if x else [])
            fig = px.sunburst(
                df, path=path, values=y, color=color,
                title=title, template=template,
            )
        elif chart_type == "treemap":
            path = kwargs.get("path", [x] if x else [])
            fig = px.treemap(
                df, path=path, values=y, color=color,
                title=title, template=template,
            )
        elif chart_type == "parallel":
            fig = px.parallel_coordinates(
                df, color=color, color_continuous_scale=kwargs.get("colorscale"),
                title=title,
            )
        else:
            raise ValueError(f"Unknown chart type: {chart_type}")

        # Update layout
        fig.update_layout(
            height=height,
            width=width,
            title_x=0.5,
            showlegend=kwargs.get("showlegend", True),
        )

        # Apply custom layout updates
        if "layout" in kwargs:
            fig.update_layout(**kwargs["layout"])

        return fig

    # ============================================================
    # Matplotlib/Seaborn Charts
    # ============================================================

    def _create_matplotlib_chart(
        self,
        df: pd.DataFrame,
        chart_type: str,
        **kwargs,
    ) -> plt.Figure:
        """Create Matplotlib/Seaborn chart."""
        fig, ax = plt.subplots(figsize=kwargs.get("figsize", (10, 6)))

        x = kwargs.get("x")
        y = kwargs.get("y")
        hue = kwargs.get("color")
        title = kwargs.get("title", "")

        if chart_type == "bar":
            sns.barplot(data=df, x=x, y=y, hue=hue, ax=ax, **kwargs.get("seaborn_kws", {}))
        elif chart_type == "line":
            sns.lineplot(data=df, x=x, y=y, hue=hue, ax=ax, **kwargs.get("seaborn_kws", {}))
        elif chart_type == "scatter":
            sns.scatterplot(data=df, x=x, y=y, hue=hue, ax=ax, **kwargs.get("seaborn_kws", {}))
        elif chart_type == "histogram":
            sns.histplot(data=df, x=x, hue=hue, ax=ax, **kwargs.get("seaborn_kws", {}))
        elif chart_type == "box":
            sns.boxplot(data=df, x=x, y=y, hue=hue, ax=ax, **kwargs.get("seaborn_kws", {}))
        elif chart_type == "violin":
            sns.violinplot(data=df, x=x, y=y, hue=hue, ax=ax, **kwargs.get("seaborn_kws", {}))
        elif chart_type == "heatmap":
            z = kwargs.get("z")
            if z and z in df.columns and kwargs.get("x") and kwargs.get("y"):
                pivot = df.pivot_table(values=z, index=kwargs["y"], columns=kwargs["x"])
            else:
                pivot = df.select_dtypes(include="number").corr()
            sns.heatmap(pivot, annot=True, fmt=".2f", cmap=kwargs.get("cmap", "RdBu_r"), ax=ax)
        elif chart_type == "pairplot":
            return sns.pairplot(df, hue=hue, **kwargs.get("seaborn_kws", {})).fig
        else:
            raise ValueError(f"Unknown chart type for matplotlib: {chart_type}")

        ax.set_title(title)
        plt.tight_layout()
        return fig

    def _mpl_to_html(self, fig: plt.Figure) -> str:
        """Convert matplotlib figure to HTML."""
        buf = BytesIO()
        fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
        buf.seek(0)
        img_base64 = base64.b64encode(buf.read()).decode("utf-8")
        plt.close(fig)
        return f'<img src="data:image/png;base64,{img_base64}" style="max-width:100%"/>'

    # ============================================================
    # High-level chart methods
    # ============================================================

    def bar(
        self,
        df: pd.DataFrame,
        x: str,
        y: str,
        color: Optional[str] = None,
        **kwargs,
    ) -> VizResult:
        """Create bar chart."""
        return self.create_chart(df, "bar", x=x, y=y, color=color, **kwargs)

    def line(
        self,
        df: pd.DataFrame,
        x: str,
        y: str,
        color: Optional[str] = None,
        **kwargs,
    ) -> VizResult:
        """Create line chart."""
        return self.create_chart(df, "line", x=x, y=y, color=color, **kwargs)

    def scatter(
        self,
        df: pd.DataFrame,
        x: str,
        y: str,
        color: Optional[str] = None,
        size: Optional[str] = None,
        **kwargs,
    ) -> VizResult:
        """Create scatter plot."""
        return self.create_chart(df, "scatter", x=x, y=y, color=color, size=size, **kwargs)

    def histogram(
        self,
        df: pd.DataFrame,
        x: str,
        **kwargs,
    ) -> VizResult:
        """Create histogram."""
        return self.create_chart(df, "histogram", x=x, **kwargs)

    def box(
        self,
        df: pd.DataFrame,
        x: Optional[str] = None,
        y: str = None,
        color: Optional[str] = None,
        **kwargs,
    ) -> VizResult:
        """Create box plot."""
        return self.create_chart(df, "box", x=x, y=y, color=color, **kwargs)

    def heatmap(
        self,
        df: pd.DataFrame,
        x: Optional[str] = None,
        y: Optional[str] = None,
        z: Optional[str] = None,
        **kwargs,
    ) -> VizResult:
        """Create heatmap (correlation or pivot)."""
        return self.create_chart(df, "heatmap", x=x, y=y, z=z, **kwargs)

    def correlation_heatmap(
        self,
        df: pd.DataFrame,
        method: str = "pearson",
        **kwargs,
    ) -> VizResult:
        """Create correlation heatmap."""
        numeric = df.select_dtypes(include="number")
        if numeric.empty:
            return VizResult(success=False, error="No numeric columns for correlation")

        corr = numeric.corr(method=method)
        # Pass the correlation matrix directly to heatmap via z param
        return self.create_chart(
            corr.reset_index().melt(id_vars="index", var_name="variable", value_name="correlation"),
            "heatmap",
            x="variable", y="index", z="correlation",
            **kwargs,
        )

    def pairplot(
        self,
        df: pd.DataFrame,
        hue: Optional[str] = None,
        **kwargs,
    ) -> VizResult:
        """Create pair plot (matplotlib only)."""
        if self.default_engine != "matplotlib":
            logger.warning("Pairplot only works with matplotlib engine, switching temporarily")
            old_engine = self.default_engine
            self.default_engine = "matplotlib"
            result = self.create_chart(df, "pairplot", color=hue, **kwargs)
            self.default_engine = old_engine
            return result
        return self.create_chart(df, "pairplot", color=hue, **kwargs)

    # ============================================================
    # Export methods
    # ============================================================

    def save_html(self, result: VizResult, filename: str) -> str:
        """Save chart as HTML file."""
        if not result.success or not result.html:
            raise ValueError("No valid chart to save")

        path = self.output_dir / f"{filename}.html"
        path.write_text(result.html)
        logger.info(f"Saved chart to {path}")
        return str(path)

    def save_png(self, result: VizResult, filename: str, scale: int = 2) -> str:
        """Save chart as PNG."""
        if not result.success or result.figure is None:
            raise ValueError("No valid chart to save")

        path = self.output_dir / f"{filename}.png"

        if self.default_engine == "plotly":
            result.figure.write_image(str(path), scale=scale)
        else:
            result.figure.savefig(path, dpi=150 * scale, bbox_inches="tight")

        logger.info(f"Saved chart to {path}")
        return str(path)

    def save_json(self, result: VizResult, filename: str) -> str:
        """Save Plotly chart as JSON."""
        if not result.success or result.figure is None:
            raise ValueError("No valid chart to save")

        path = self.output_dir / f"{filename}.json"
        if self.default_engine == "plotly":
            path.write_text(result.figure.to_json())
        else:
            # For matplotlib, save config
            path.write_text(json.dumps(result.config))
        return str(path)

    def to_base64_png(self, result: VizResult) -> str:
        """Convert chart to base64 PNG string."""
        if not result.success or result.figure is None:
            return ""

        buf = BytesIO()
        if self.default_engine == "plotly":
            result.figure.write_image(buf, format="png", scale=2)
        else:
            result.figure.savefig(buf, format="png", dpi=300, bbox_inches="tight")
        buf.seek(0)
        return base64.b64encode(buf.read()).decode("utf-8")


class VizToolOpenAI:
    """OpenAI function calling format for Viz tool."""

    @staticmethod
    def get_function_definitions() -> List[Dict[str, Any]]:
        """Get OpenAI function definitions."""
        return [
            {
                "type": "function",
                "function": {
                    "name": "create_chart",
                    "description": "Create a visualization from DataFrame data",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "df_name": {"type": "string", "description": "Registered DataFrame name"},
                            "chart_type": {
                                "type": "string",
                                "enum": ["bar", "line", "scatter", "histogram", "box", "heatmap", "pie", "area", "violin"],
                            },
                            "x": {"type": "string"},
                            "y": {"type": "string"},
                            "color": {"type": "string"},
                            "title": {"type": "string"},
                        },
                        "required": ["df_name", "chart_type"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "correlation_heatmap",
                    "description": "Create correlation heatmap from numeric columns",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "df_name": {"type": "string"},
                            "method": {"type": "string", "default": "pearson"},
                        },
                        "required": ["df_name"],
                    },
                },
            },
        ]


# Convenience function
def create_chart(
    df: pd.DataFrame,
    chart_type: str,
    output_dir: str = "outputs/charts",
    **kwargs,
) -> VizResult:
    """Convenience function to create a chart."""
    tool = VizTool(output_dir=output_dir)
    return tool.create_chart(df, chart_type, **kwargs)