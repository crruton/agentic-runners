"""Аттестация проверок агента (ADR-0007): CI не перепроверяет то, что агент уже проверил у себя.

Агент выполняет проверку (тесты, линт) на закоммиченном дереве и записывает подписанное свидетельство
в git note (refs/notes/agentic-attest) на свой коммит, затем пушит его вместе с веткой. CI вызывает ту же
команду через `verify`: если свидетельство на это дерево, эту команду и такое же окружение подписано
действующим ключом и коммит не попал в аудиторскую выборку, задание завершается сразу. Иначе команда
выполняется как обычно. Аудит — детерминированная доля коммитов, которые CI проверяет полностью,
даже при наличии свидетельства: так любая подделка или невоспроизводимость обнаруживается.

    python -m agentic_runtime.attest run    -- pytest -q        # у агента
    python -m agentic_runtime.attest verify -- pytest -q        # в CI, внутри задания
    python -m agentic_runtime.attest gate ci/agentic-checks.yml child.yml   # в CI, шлюз пайплайна
    python -m agentic_runtime.attest run --image node:20-alpine@sha256:... -- "npm ci && npm test"
                                                                # у агента, режим образа (любой язык)

Окружение: AGENTIC_ATTEST_KEY (секрет подписи, HMAC-SHA256), AGENTIC_ATTEST_AUDIT_PCT (по умолчанию 10),
AGENTIC_ATTEST_ENV (необязательно: строка окружения сверх автоматической, например digest образа CI).
"""

import hashlib
import hmac
import json
import os
import platform
import subprocess
import sys
import time

NOTES = "refs/notes/agentic-attest"
VERSION = 1
LOCKS = ("uv.lock", "poetry.lock", "Pipfile.lock", "requirements.txt", "requirements-dev.txt", "pyproject.toml",
         "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "go.sum", "Cargo.lock")


def git(*args, check=True):
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                          check=check).stdout.strip()


def _locks():
    locks = {}
    for f in LOCKS:
        blob = git("rev-parse", f"HEAD:{f}", check=False)
        if blob and not blob.startswith("fatal"):
            locks[f] = blob
    return locks


def environment(image=None):
    """Отпечаток окружения.

    Режим образа (image задан, обычно с @sha256): точный образ задания CI и lock-файлы дерева — проверка
    у агента идёт в том же образе, интерпретатор самого attest роли не играет.
    Режим процесса: интерпретатор, ОС и архитектура, lock-файлы, строка AGENTIC_ATTEST_ENV.
    """
    if image:
        return {"image": image, "locks": _locks()}
    return {"python": platform.python_version(), "os": platform.system(), "arch": platform.machine(),
            "locks": _locks(), "extra": os.environ.get("AGENTIC_ATTEST_ENV", "")}


def _payload(tree, cmd, env):
    return json.dumps({"v": VERSION, "tree": tree, "cmd": cmd, "env": env}, sort_keys=True, ensure_ascii=False)


def _sign(key, payload):
    return hmac.new(key.encode(), payload.encode(), hashlib.sha256).hexdigest()


def _key():
    key = os.environ.get("AGENTIC_ATTEST_KEY")
    if not key:
        sys.exit("agentic-attest: AGENTIC_ATTEST_KEY не задан")
    return key


def audited(tree, pct):
    """Детерминированная выборка: один и тот же коммит всегда попадает или не попадает в аудит."""
    return int(hashlib.sha256(("audit:" + tree).encode()).hexdigest(), 16) % 100 < pct


def _run_in_image(image, script):
    """Выполнить команду в образе CI на копии закоммиченного дерева (git archive HEAD), не на рабочей копии."""
    import io as _io
    import shutil
    import tarfile
    import tempfile

    work = tempfile.mkdtemp(prefix="agentic-attest-")
    try:
        data = subprocess.run(["git", "archive", "--format=tar", "HEAD"], capture_output=True, check=True).stdout
        with tarfile.open(fileobj=_io.BytesIO(data)) as tar:
            if hasattr(tarfile, "data_filter"):
                tar.extractall(work, filter="data")
            else:  # Python до 3.12: архив из собственного git, доверенный
                tar.extractall(work)
        rc = subprocess.call(["docker", "run", "--rm", "--label", "agentic-attest=1", "-v", f"{work}:/w", "-w", "/w",
                              image, "sh", "-c", script])
        # файлы, созданные в контейнере от root, удаляет сам контейнер
        subprocess.call(["docker", "run", "--rm", "--label", "agentic-attest=1", "-v", f"{work}:/w", image,
                         "sh", "-c", "rm -rf /w/* /w/.[!.]* 2>/dev/null; true"], stdout=subprocess.DEVNULL)
        return rc
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run(cmd, image=None):
    """У агента: выполнить проверку на чистом закоммиченном дереве и записать свидетельство при успехе.

    С image команда — одна строка для sh -c и выполняется в этом образе на копии дерева коммита.
    """
    if git("status", "--porcelain", "--untracked-files=no"):
        sys.exit("agentic-attest: есть незакоммиченные изменения — свидетельство описывает только коммит")
    tree, env = git("rev-parse", "HEAD^{tree}"), environment(image)
    t0 = time.time()
    rc = _run_in_image(image, cmd[0]) if image else subprocess.call(cmd)
    if rc != 0:
        return rc
    payload = _payload(tree, cmd, env)
    note = {"payload": json.loads(payload), "sig": _sign(_key(), payload),
            "who": os.environ.get("AGENTIC_ATTEST_WHO", platform.node()), "seconds": round(time.time() - t0, 1),
            "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    existing = git("notes", "--ref", NOTES, "show", "HEAD", check=False)
    notes = json.loads(existing) if existing.startswith("[") else []
    notes = [n for n in notes if n["payload"]["cmd"] != cmd] + [note]
    git("notes", "--ref", NOTES, "add", "-f", "-m", json.dumps(notes, ensure_ascii=False), "HEAD")
    print(f"agentic-attest: свидетельство записано для дерева {tree[:12]}; запушьте {NOTES} вместе с веткой")
    return 0


def check(cmd, image=None):
    """Причина, по которой свидетельство не принимается, или None, если принимается."""
    raw = git("notes", "--ref", NOTES, "show", "HEAD", check=False)
    if not raw.startswith("["):
        return "нет свидетельства"
    tree, env = git("rev-parse", "HEAD^{tree}"), environment(image)
    want = _payload(tree, cmd, env)
    for note in json.loads(raw):
        if note["payload"]["cmd"] != cmd:
            continue
        got = json.dumps(note["payload"], sort_keys=True, ensure_ascii=False)
        if got != want:
            return _mismatch(note["payload"], tree, env)
        key = os.environ.get("AGENTIC_ATTEST_KEY")
        if not key:  # ключ ещё не заведён в CI: проверки идут как раньше, пайплайн не ломается
            return "в CI не задан AGENTIC_ATTEST_KEY"
        if not hmac.compare_digest(note["sig"], _sign(key, got)):
            return "подпись недействительна"
        return None
    return "нет свидетельства для этой команды"


def _mismatch(payload, tree, env):
    """Какое поле свидетельства не совпало с CI — чтобы расхождение было видно и чинилось."""
    if payload.get("tree") != tree:
        return "дерево не совпадает"
    diffs = []
    for k in sorted(set(env) | set(payload.get("env", {}))):
        a, b = payload.get("env", {}).get(k), env.get(k)
        if a != b:
            diffs.append(f"{k}: агент {a!r}, CI {b!r}")
    return "окружение не совпадает — " + "; ".join(diffs) if diffs else "версия формата не совпадает"


def verify(cmd):
    """В CI: принять свидетельство или выполнить команду (нет свидетельства, не совпало, аудит)."""
    git("fetch", "-q", "origin", f"+{NOTES}:{NOTES}", check=False)
    tree = git("rev-parse", "HEAD^{tree}")
    pct = int(os.environ.get("AGENTIC_ATTEST_AUDIT_PCT", "10"))
    why = check(cmd)
    if why is None and not audited(tree, pct):
        print(f"agentic-attest: проверка принята по свидетельству агента (дерево {tree[:12]}), команда не выполняется")
        return 0
    reason = "аудит" if why is None else why
    print(f"agentic-attest: выполняю команду ({reason})")
    rc = subprocess.call(cmd)
    if why is None and rc != 0:
        print("agentic-attest: АУДИТ НЕ СОШЁЛСЯ — свидетельство подтверждало успех, проверка упала", file=sys.stderr)
    return rc


def gate(checks_path, out_path):
    """Шлюз пайплайна: одно задание решает, какие проверки запускать в дочернем пайплайне.

    checks_path — YAML с заданиями проверок проекта; задание, у которого в variables есть AGENTIC_CMD,
    считается проверкой. Принятые по свидетельству и не попавшие в аудит выключаются (rules: when: never).
    Возвращает словарь «имя задания → причина запуска или None».
    """
    import shlex

    import yaml

    git("fetch", "-q", "origin", f"+{NOTES}:{NOTES}", check=False)
    tree = git("rev-parse", "HEAD^{tree}")
    pct = int(os.environ.get("AGENTIC_ATTEST_AUDIT_PCT", "10"))
    with open(checks_path, encoding="utf-8") as f:
        jobs = yaml.safe_load(f) or {}
    decisions, child = {}, {"include": [{"local": checks_path}]}
    for name, job in jobs.items():
        cmd = (job or {}).get("variables", {}).get("AGENTIC_CMD") if isinstance(job, dict) else None
        if not cmd:
            continue
        image = job.get("image")
        image = image.get("name") if isinstance(image, dict) else image
        # образ, закреплённый по digest, — режим образа: агент проверял ровно в нём
        why = check([cmd], image) if image and "@sha256:" in str(image) else check(shlex.split(cmd))
        if why is None and not audited(tree, pct):
            decisions[name] = None
            child[name] = {"rules": [{"when": "never"}]}
        else:
            decisions[name] = "аудит" if why is None else why
    accepted = [n for n, d in decisions.items() if d is None]
    child["attest:summary"] = {"image": "alpine:3.20", "script": [
        f"echo 'принято по свидетельству: {', '.join(accepted) or '—'}'",
        f"echo 'запускается: {', '.join(n for n, d in decisions.items() if d) or '—'}'"]}
    with open(out_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(child, f, allow_unicode=True, sort_keys=False)
    for n, d in decisions.items():
        print(f"agentic-attest gate: {n}: {'принято по свидетельству' if d is None else 'запуск — ' + d}")
    return decisions


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 3 and argv[0] == "gate":
        gate(argv[1], argv[2])
        return 0
    if len(argv) == 5 and argv[0] == "run" and argv[1] == "--image" and argv[3] == "--":
        return run([argv[4]], argv[2])
    if len(argv) < 3 or argv[0] not in ("run", "verify") or argv[1] != "--":
        sys.exit("использование: python -m agentic_runtime.attest run|verify -- <команда> | "
                 "run --image <образ@sha256:...> -- \"<команда sh>\" | gate <checks.yml> <child.yml>")
    cmd = argv[2:]
    return (run if argv[0] == "run" else verify)(cmd)


if __name__ == "__main__":
    sys.exit(main())
