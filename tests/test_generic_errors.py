"""TDD: client errors must be generic, details server-side only."""
import inspect


def test_upload_load_errors_generic():
    from api.routes import data as d
    src = inspect.getsource(d.upload_file) + inspect.getsource(d.load_to_warehouse)
    assert 'detail=f"Failed to process file: {e}"' not in src
    assert 'detail=f"Load failed: {e}"' not in src
    assert "Failed to process file" in src  # generic still present
    assert "Load failed" in src


def test_clean_preview_error_generic():
    from api.routes import data as d
    src = inspect.getsource(d.clean_preview)
    assert '"error": str(e)' not in src and "'error': str(e)" not in src


def test_google_login_error_generic():
    from api import google_login as gl
    src = inspect.getsource(gl.GoogleLoginManager.handle_callback)
    assert 'f"Sign-in failed: {e}' not in src
    assert "Sign-in failed" in src
