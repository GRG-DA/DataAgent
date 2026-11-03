"""Role defaults for identity records.

Every fall-through for a record with a missing role must land on the
self-serve standard ("user") — the same default self-registration grants —
not on the read-only "viewer". A role-less record used to surface as
viewer, which silently locked its owner out of dashboard creation and
other standard actions.
"""


def test_public_info_missing_role_defaults_to_user():
    from api.auth import user_manager

    info = user_manager._public_info({"user_id": "u1"}, "legacy")
    assert info["role"] == "user"


def test_public_info_keeps_existing_roles():
    from api.auth import user_manager

    assert user_manager._public_info({"role": "viewer"}, "v")["role"] == "viewer"
    assert user_manager._public_info({"role": "user"}, "u")["role"] == "user"
    assert user_manager._public_info({"role": "admin"}, "a")["role"] == "admin"


def test_google_login_config_defaults_to_user():
    from api.google_login import GoogleLoginConfig

    assert GoogleLoginConfig().default_role == "user"


def test_legacy_import_missing_or_blank_role_becomes_self_serve_user():
    """Legacy users.json rows predate roles; they were de-facto self-serve
    users and must not import as read-only after an upgrade."""
    from api.user_store import _record_from_legacy

    assert _record_from_legacy("alice", {}).role == "user"
    assert _record_from_legacy("bob", {"role": ""}).role == "user"


def test_legacy_import_keeps_explicit_roles():
    from api.user_store import _record_from_legacy

    assert _record_from_legacy("carol", {"role": "viewer"}).role == "viewer"
    assert _record_from_legacy("dave", {"role": "user"}).role == "user"


def test_user_record_default_role_is_self_serve():
    from api.user_store import UserRecord

    assert UserRecord("person").role == "user"


def test_central_provision_nonfirst_account_is_self_serve(monkeypatch):
    """Billing/checkout ('central accounts') sign-ups get the same self-serve
    role as local self-registration; only the very first account becomes
    admin (bootstrap-gated)."""
    from api import auth as auth_mod
    from api import central_accounts

    class FakeManager:
        def __init__(self):
            self.added = []
            self._users = {"admin-one": {"username": "admin-one"}}

        def get_user_by_email(self, email):
            return None

        def get_user_info(self, username):
            return None

        def get_users(self):
            return list(self._users.values())

        def add_user(self, **kwargs):
            self.added.append(kwargs)
            return "uid-new"

    fake = FakeManager()
    monkeypatch.setattr(auth_mod, "user_manager", fake)
    central_accounts.provision_user("checkout@example.com", "sub-1")

    assert fake.added, "provision_user should create the account"
    assert fake.added[0]["role"] == "user"
    assert fake.added[0]["sso_provider"] == central_accounts.SSO_PROVIDER


def test_shipped_yaml_google_login_role_is_user():
    """The google_login block in config/settings.yaml ships "user", the
    self-serve standard (rbac.default_role is also "user")."""
    from api.google_login import _load_google_login_config

    cfg = _load_google_login_config()
    assert cfg.default_role == "user"
