"""Аттестация проверок агента (ADR-0007): свидетельство в git note, проверка в CI, аудит."""

import os
import subprocess
import sys

import pytest

RUNTIME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RUNTIME)
from agentic_runtime import attest  # noqa: E402

MARK = "ran.txt"
CMD = [sys.executable, "-c", f"open({MARK!r}, 'a').write('x')"]


def sh(cwd, *args):
    subprocess.run(list(args), cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repos(tmp_path, monkeypatch):
    origin, agent, ci = tmp_path / "origin.git", tmp_path / "agent", tmp_path / "ci"
    sh(tmp_path, "git", "init", "-q", "--bare", str(origin))
    sh(tmp_path, "git", "clone", "-q", str(origin), str(agent))
    for k, v in (("user.email", "a@a"), ("user.name", "agent")):
        sh(agent, "git", "config", k, v)
    (agent / "app.py").write_text("X = 1\n", encoding="utf-8")
    (agent / ".gitignore").write_text(MARK + "\n", encoding="utf-8")
    sh(agent, "git", "add", "-A")
    sh(agent, "git", "commit", "-qm", "init")
    monkeypatch.setenv("AGENTIC_ATTEST_KEY", "secret")
    monkeypatch.setenv("AGENTIC_ATTEST_AUDIT_PCT", "0")
    monkeypatch.delenv("AGENTIC_ATTEST_ENV", raising=False)
    return origin, agent, ci


def attest_and_push(agent):
    os.chdir(agent)
    assert attest.run(CMD) == 0
    sh(agent, "git", "push", "-q", "origin", "HEAD", attest.NOTES)


def ci_verify(origin, ci):
    if not ci.exists():
        sh(ci.parent, "git", "clone", "-q", str(origin), str(ci))
    else:
        sh(ci, "git", "pull", "-q")
    os.chdir(ci)
    (ci / MARK).unlink(missing_ok=True)
    rc = attest.verify(CMD)
    return rc, (ci / MARK).exists()


def test_attested_commit_is_not_rerun(repos):
    origin, agent, ci = repos
    attest_and_push(agent)
    assert ci_verify(origin, ci) == (0, False)


def test_new_commit_without_attestation_runs(repos):
    origin, agent, ci = repos
    attest_and_push(agent)
    (agent / "app.py").write_text("X = 2\n", encoding="utf-8")
    sh(agent, "git", "commit", "-qam", "change")
    sh(agent, "git", "push", "-q", "origin", "HEAD")
    assert ci_verify(origin, ci) == (0, True)


def test_wrong_key_runs(repos, monkeypatch):
    origin, agent, ci = repos
    attest_and_push(agent)
    monkeypatch.setenv("AGENTIC_ATTEST_KEY", "other")
    assert ci_verify(origin, ci) == (0, True)


def test_different_environment_runs(repos, monkeypatch):
    origin, agent, ci = repos
    attest_and_push(agent)
    monkeypatch.setenv("AGENTIC_ATTEST_ENV", "image@sha256:ci")
    assert ci_verify(origin, ci) == (0, True)


def test_audit_reruns_attested_commit(repos, monkeypatch):
    origin, agent, ci = repos
    attest_and_push(agent)
    monkeypatch.setenv("AGENTIC_ATTEST_AUDIT_PCT", "100")
    assert ci_verify(origin, ci) == (0, True)


def test_failed_check_leaves_no_attestation(repos):
    origin, agent, ci = repos
    os.chdir(agent)
    assert attest.run([sys.executable, "-c", "raise SystemExit(1)"]) == 1
    assert attest.check(CMD) == "нет свидетельства"


def test_gate_disables_attested_checks(repos, tmp_path):
    yaml = pytest.importorskip("yaml")
    origin, agent, ci = repos
    attest_and_push(agent)
    sh(ci.parent, "git", "clone", "-q", str(origin), str(ci))
    os.chdir(ci)
    checks = ci / "checks.yml"
    import shlex
    checks.write_text(yaml.safe_dump({
        "tests": {"script": ["true"], "variables": {"AGENTIC_CMD": shlex.join(CMD)}},
        "lint": {"script": ["true"], "variables": {"AGENTIC_CMD": "ruff check ."}},
        "build": {"script": ["true"]},
    }), encoding="utf-8")
    decisions = attest.gate(str(checks), str(tmp_path / "child.yml"))
    assert decisions == {"tests": None, "lint": "нет свидетельства для этой команды"}
    child = yaml.safe_load((tmp_path / "child.yml").read_text(encoding="utf-8"))
    assert child["tests"] == {"rules": [{"when": "never"}]} and "lint" not in child
    assert "attest:summary" in child


def test_mismatch_names_the_field(repos, monkeypatch):
    origin, agent, ci = repos
    attest_and_push(agent)
    sh(ci.parent, "git", "clone", "-q", str(origin), str(ci))
    os.chdir(ci)
    sh(ci, "git", "fetch", "-q", "origin", f"+{attest.NOTES}:{attest.NOTES}")
    monkeypatch.setenv("AGENTIC_ATTEST_ENV", "image@sha256:ci")
    reason = attest.check(CMD)
    assert reason.startswith("окружение не совпадает") and "extra" in reason
