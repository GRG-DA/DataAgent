"""UI Package - Professional Design System for LLM Data Analytics Agent."""

from .design_tokens import DESIGN_TOKENS_CSS, DARK_TOKENS_CSS, COMPONENT_STYLES_CSS, UTILITY_CLASSES_CSS
from .utils.css_injector import inject_design_system, inject_page_css

__all__ = [
    "DESIGN_TOKENS_CSS",
    "DARK_TOKENS_CSS",
    "COMPONENT_STYLES_CSS",
    "UTILITY_CLASSES_CSS",
    "inject_design_system",
    "inject_page_css",
]