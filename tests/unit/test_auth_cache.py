"""设计审查 P1-10：_build_user_dict 的 roles/permissions 查询按 user 做进程内 TTL 缓存。

旧实现每个鉴权请求都打 3 次 DB（user + roles + permissions）。缓存命中后
同一 60s 窗口内不再打 DB；invalidate_user_cache 使角色/权限变更立即可见。
"""
from types import SimpleNamespace

import app.middleware.auth as auth_mod


def _user(uid: str = "u1"):
    return SimpleNamespace(id=uid, username="alice", display_name="Alice")


def _setup(monkeypatch, user_id="u1"):
    auth_mod._user_cache.clear()
    calls = {"n": 0}

    def fake_role_ids(uid):
        calls["n"] += 1
        return [1]

    def fake_perms(uid):
        return ["chat"]

    monkeypatch.setattr(auth_mod, "get_user_role_ids", fake_role_ids)
    monkeypatch.setattr(auth_mod, "get_user_permissions", fake_perms)
    monkeypatch.setattr(auth_mod, "_get_admin_role_id", lambda: 1)
    return calls


def test_user_dict_cached_within_ttl(monkeypatch):
    calls = _setup(monkeypatch)
    u = _user()
    d1 = auth_mod._build_user_dict(u)
    d2 = auth_mod._build_user_dict(u)
    assert calls["n"] == 1, "第二次调用应命中缓存，不再打 DB"
    assert d1 is d2


def test_invalidate_clears_cache(monkeypatch):
    calls = _setup(monkeypatch)
    u = _user()
    auth_mod._build_user_dict(u)
    assert calls["n"] == 1
    auth_mod.invalidate_user_cache("u1")
    auth_mod._build_user_dict(u)
    assert calls["n"] == 2, "失效后重新查询角色/权限"


def test_admin_role_cached_and_invalidated(monkeypatch):
    """设计审查 P3-18：_get_admin_role_id 缓存 + invalidate_admin_role 失效。"""
    import app.store.db as db_mod

    auth_mod._admin_role_id = None
    auth_mod._admin_role_ts = 0.0
    calls = {"n": 0}

    class _Role:
        id = 7

    class _Filter:
        def first(self):
            calls["n"] += 1
            return _Role

    class _Query:
        def filter(self, *a, **k):
            return _Filter()

    class _FakeSession:
        def query(self, *a, **k):
            return _Query()

        def close(self):
            pass

    monkeypatch.setattr(db_mod, "get_session", _FakeSession)

    assert auth_mod._get_admin_role_id() == 7
    assert auth_mod._get_admin_role_id() == 7     # 命中缓存，不重查
    assert calls["n"] == 1

    auth_mod.invalidate_admin_role()
    assert auth_mod._get_admin_role_id() == 7     # 失效后重查
    assert calls["n"] == 2


def test_cache_expires_after_ttl(monkeypatch):
    calls = _setup(monkeypatch)
    u = _user()
    auth_mod._build_user_dict(u)
    assert calls["n"] == 1
    # 把缓存时间戳拨老，验证 TTL 过期后重新查询
    old_ts = auth_mod._user_cache["u1"][0] - auth_mod._USER_CACHE_TTL - 1
    auth_mod._user_cache["u1"] = (old_ts, auth_mod._user_cache["u1"][1])
    auth_mod._build_user_dict(u)
    assert calls["n"] == 2, "TTL 过期后应重新查询"