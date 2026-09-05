"""Private token initialization and crash-safe state writes."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from jobpilot.core import atomic_io, config, server_auth
from jobpilot.core.profile_store import ProfileStore, UserProfile


def test_concurrent_servers_share_one_private_persisted_token(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SERVER_AUTH_TOKEN", "")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        tokens = list(pool.map(lambda _: server_auth.get_auth_token(), range(16)))
    assert len(set(tokens)) == 1
    path = tmp_path / "server_token"
    assert path.read_text() == tokens[0]
    assert path.stat().st_mode & 0o777 == 0o600
    assert server_auth.get_auth_token() == tokens[0]


def test_configured_token_does_not_create_a_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SERVER_AUTH_TOKEN", "configured-test-token")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    assert server_auth.get_auth_token() == "configured-test-token"
    assert not list(tmp_path.iterdir())


def test_failed_atomic_replace_preserves_old_state(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text("original")

    def fail(*args):
        raise OSError("simulated crash before replacement")

    monkeypatch.setattr(atomic_io.os, "replace", fail)
    with pytest.raises(OSError):
        atomic_io.atomic_write_text(path, "replacement")
    assert path.read_text() == "original"
    assert list(tmp_path.iterdir()) == [path]


def test_missing_relocation_preference_defaults_to_no(tmp_path):
    assert UserProfile().open_to_relocation is False
    store = ProfileStore(data_dir=tmp_path)
    store.save(UserProfile(open_to_relocation=True))
    assert store.load().open_to_relocation is True
