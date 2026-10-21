"""TDD: importing api.org/dependencies must not RW-open warehouse."""
import inspect


def test_import_does_not_init_tables_at_import(tmp_path):
    from api.org import OrgManager
    # Fresh instance must not touch DB at construction (lazy init for single-writer)
    db = str(tmp_path / "fresh_import.duckdb")
    mgr = OrgManager(db_path=db)
    assert mgr._tables_ready is False
    import pathlib
    assert not pathlib.Path(db).exists(), "OrgManager() must not create DB file at import"
    src = inspect.getsource(OrgManager.__init__)
    assert "_init_tables" not in src, "__init__ must not init tables"


def test_get_org_manager_singleton_lazy():
    from api.org import get_org_manager, OrgManager
    m1 = get_org_manager()
    m2 = get_org_manager()
    assert isinstance(m1, OrgManager)
    assert m1 is m2


def test_read_methods_use_readonly():
    import inspect
    from api import org as org_mod
    src = inspect.getsource(org_mod.OrgManager._get_conn)
    assert "read_only" in src, "_get_conn must support read_only"


def test_dependencies_uses_getter_not_global():
    import inspect
    from api import dependencies as dep_mod
    src = inspect.getsource(dep_mod.get_current_user_groups)
    assert "get_org_manager()" in src, "must use get_org_manager() not global"
    # global org_manager import should be gone or unused in that function
    assert "org_manager.get_user_groups" not in src
