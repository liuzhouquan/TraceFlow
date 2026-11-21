import subprocess
import uuid
from types import SimpleNamespace

from app.core.config import settings
from app.services import code_analysis


def test_sync_checkout_uses_cached_checkout_when_update_fails(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "repo_checkout_dir", str(tmp_path))
    repo = SimpleNamespace(
        id=uuid.uuid4(),
        full_name="owner/repo",
        clone_url="https://github.com/owner/repo.git",
        default_branch="main",
    )
    checkout = code_analysis._repo_checkout_path(repo)
    (checkout / ".git").mkdir(parents=True)

    def fail_run(args, cwd=None):
        raise subprocess.CalledProcessError(128, ["git", *args], stderr="network failed")

    monkeypatch.setattr(code_analysis, "_run_git", fail_run)

    assert code_analysis._sync_checkout(repo, None) == checkout


def test_sync_checkout_uses_configured_local_repo_without_updating(tmp_path, monkeypatch) -> None:
    local_repo = tmp_path / "local-repo"
    local_repo.mkdir()
    repo = SimpleNamespace(
        id=uuid.uuid4(),
        full_name="owner/repo",
        clone_url="https://github.com/owner/repo.git",
        default_branch="main",
        local_path=str(local_repo),
        checkout_mode="local",
    )

    monkeypatch.setattr(
        code_analysis, "_is_git_work_tree", lambda path: path == local_repo.resolve()
    )

    def fail_run(args, cwd=None):
        raise AssertionError("local repository indexing should not fetch, checkout, or pull")

    monkeypatch.setattr(code_analysis, "_run_git", fail_run)

    assert code_analysis._sync_checkout(repo, None) == local_repo.resolve()


def test_sync_checkout_rejects_non_empty_configured_download_target(tmp_path, monkeypatch) -> None:
    target = tmp_path / "repo"
    target.mkdir()
    (target / "README.md").write_text("# occupied\n", encoding="utf-8")
    repo = SimpleNamespace(
        id=uuid.uuid4(),
        full_name="owner/repo",
        clone_url="https://github.com/owner/repo.git",
        default_branch="main",
        local_path=str(target),
        checkout_mode="managed",
    )

    monkeypatch.setattr(code_analysis.shutil, "which", lambda name: "git")
    monkeypatch.setattr(code_analysis, "_is_git_work_tree", lambda path: False)

    try:
        code_analysis._sync_checkout(repo, None)
    except RuntimeError as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("expected configured non-empty download target to be rejected")


def test_clone_token_is_not_persisted_in_remote_or_process_arguments(tmp_path, monkeypatch):
    import base64

    token = "test-github-secret"
    repo = SimpleNamespace(
        id=uuid.uuid4(),
        full_name="owner/repo",
        clone_url="https://github.com/owner/repo.git",
        default_branch="main",
    )
    monkeypatch.setattr(settings, "repo_checkout_dir", str(tmp_path))
    calls = []
    monkeypatch.setattr(code_analysis, "_run_git", lambda args, **kw: calls.append((args, kw)))
    code_analysis._sync_checkout(repo, token)
    args, options = calls[0]
    assert token not in " ".join(args)
    assert "https://github.com/owner/repo.git" in args
    env = options["env"]
    count = int(env["GIT_CONFIG_COUNT"]) - 1
    assert env[f"GIT_CONFIG_KEY_{count}"] == "http.https://github.com/owner/repo.git.extraHeader"
    expected = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    assert env[f"GIT_CONFIG_VALUE_{count}"] == f"Authorization: Basic {expected}"


def test_existing_checkout_uses_current_token_without_persisting_it(tmp_path, monkeypatch):
    repo = SimpleNamespace(
        id=uuid.uuid4(),
        full_name="owner/repo",
        clone_url="https://old-user:old-secret@github.com/owner/repo.git",
        default_branch="main",
    )
    monkeypatch.setattr(settings, "repo_checkout_dir", str(tmp_path))
    checkout = code_analysis._repo_checkout_path(repo)
    (checkout / ".git").mkdir(parents=True)
    calls = []
    monkeypatch.setattr(code_analysis, "_run_git", lambda args, **kw: calls.append((args, kw)))
    code_analysis._sync_checkout(repo, "new-secret")
    assert calls[0][0] == ["remote", "set-url", "origin", "https://github.com/owner/repo.git"]
    for args, options in calls:
        assert "secret" not in " ".join(args)
        if args[0] in {"fetch", "pull"}:
            assert "Authorization: Basic " in str(options["env"])


def test_git_credentials_reject_untrusted_or_unencrypted_http_remotes():
    import pytest

    for url in ["https://example.com/owner/repo.git", "http://github.com/owner/repo.git"]:
        with pytest.raises(ValueError, match="HTTPS"):
            code_analysis._git_auth_env(url, "secret")
    assert "GIT_CONFIG_COUNT" in code_analysis._git_auth_env(
        "https://git.example.com/owner/repo.git", "secret", "https://git.example.com/api/v3"
    )
