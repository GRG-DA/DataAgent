"""DataAgent desktop shell.

A thin pywebview window that embeds the FastAPI-served SPA
(http://127.0.0.1:<port>/) with the backend running in-process, so the
product runs as a desktop application instead of in a browser.
"""

__version__ = "1.0.0"
SHELL_NAME = "DataAgent"