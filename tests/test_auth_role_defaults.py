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


def test_shipped_yaml_google_login_role_is_user():
    """The google_login block in config/settings.yaml ships "user", the
    self-serve standard (rbac.default_role is also "user")."""
    from api.google_login import _load_google_login_config

    cfg = _load_google_login_config()
    assert cfg.default_role == "user"
