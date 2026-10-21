"""TDD: warnings must be gone."""
import warnings


def test_no_authlib_jose_import():
    import api.sso as sso_mod
    import inspect
    src = inspect.getsource(sso_mod)
    assert "from authlib.jose" not in src, "must not import deprecated authlib.jose"
    assert "joserfc" in src, "must use joserfc"


def test_upload_response_no_shadow_warning():
    import inspect
    from api.routes import data as data_mod
    src = inspect.getsource(data_mod.UploadResponse)
    assert 'alias="schema"' in src or "alias='schema'" in src, "UploadResponse must alias schema"
    assert "schema_info" in src
    from api.routes.data import UploadResponse
    # both alias and field name work
    r1 = UploadResponse(success=True, filename="a.csv", **{"schema": {"a": 1}})
    assert r1.schema_info == {"a": 1}
    assert r1.schema == {"a": 1}
    r2 = UploadResponse(success=True, filename="b.csv", schema_info={"x": 2})
    assert r2.model_dump(by_alias=True).get("schema") == {"x": 2}
