"""CLI: python -m runners {snapshot,import-legacy,report}."""

import argparse
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog="runners", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="снять БД ci_analytics и историю GitLab в data/snapshots/<дата>/")
    s.add_argument("--root", default="data/snapshots")
    s.add_argument("--days", type=int, default=30, help="глубина выгрузки GitLab")
    s.add_argument("--only", choices=["ci_analytics", "gitlab", "gitlab_pipelines", "job_resources"])

    li = sub.add_parser("import-legacy", help="перенести старый снимок и CSV в новую структуру")
    li.add_argument("--db-dir", default="data/ci_analytics/20261109")
    li.add_argument("--csv", default="data/jobs_30d.csv")
    li.add_argument("--root", default="data/snapshots")

    r = sub.add_parser("report", help="построить out/report.html и out/metrics.json по снимку (офлайн)")
    r.add_argument("--root", default="data/snapshots")
    r.add_argument("--snapshot", help="YYYYMMDD, по умолчанию последний")
    r.add_argument("--out", default="out")

    b = sub.add_parser("bench", help="A/B конфигураций раннера на эмулированном ноутбуке (нужен Docker)")
    b.add_argument("--only", help="сценарии через запятую, по умолчанию все")
    b.add_argument("--root", default="data/bench")
    b.add_argument("--resume", help="каталог прогона YYYYMMDD-HHMM: досчитать недостающие сценарии")
    af = sub.add_parser("affected", help="доля тестового времени на каждое изменение по истории git (H4, H10)")
    af.add_argument("--repo", required=True, help="локальный клон проекта (только чтение)")
    af.add_argument("--since", required=True, help="дата начала истории, YYYY-MM-DD")
    af.add_argument("--weights", action="append", required=True, help="JSON с секундами файлов тестов")
    af.add_argument("--invalidate", action="append", default=[], help="glob: изменение затрагивает все тесты")
    af.add_argument("--suite", action="append", default=[], help="префикс_тестов=glob, например tests/dbt/=dbt/*")
    af.add_argument("--per-commit", action="store_true", help="по каждому коммиту, а не по влитым изменениям")
    af.add_argument("--out", help="CSV с результатом по коммитам")
    sub.add_parser("numbers", help="docs/talk/numbers.md из out/metrics.json")
    sub.add_parser("check", help="ссылки на показатели в docs/ существуют, numbers.md свежий")
    a = sub.add_parser("all", help="report + numbers + check: весь офлайн-процесс одной командой")
    a.add_argument("--root", default="data/snapshots")
    a.add_argument("--snapshot")

    args = ap.parse_args(argv)
    if args.cmd == "affected":
        from runners import affected
        suites = {}
        for s in args.suite:
            prefix, _, glob = s.partition("=")
            suites.setdefault(prefix, []).append(glob)
        rules = {"invalidate": args.invalidate, "suites": suites, "references": True}
        df = affected.analyze(args.repo, args.since, affected.load_weights(args.weights), rules,
                              first_parent=not args.per_commit)
        if args.out:
            df.to_csv(args.out, index=False)
        print(affected.summary(df))
        return
    if args.cmd == "bench":
        from runners import bench
        bench.run(args.only.split(",") if args.only else None, args.root, args.resume)
        return
    if args.cmd in ("numbers", "check", "all"):
        from runners import docs
        if args.cmd == "all":
            from runners.load import SnapshotMissing, load
            from runners.report import build
            try:
                print(*build(load(args.root, args.snapshot), "out"), sep="\n")
            except SnapshotMissing as e:
                sys.exit(f"{e}. Сначала снимите данные: python -m runners snapshot")
        if args.cmd in ("numbers", "all"):
            print(docs.numbers())
        problems = docs.check() if args.cmd in ("check", "all") else []
        for p in problems:
            print(p, file=sys.stderr)
        sys.exit(1 if problems else 0)
    if args.cmd == "snapshot":
        from runners.snapshot import take
        sys.exit(take(args.root, args.days, args.only))
    if args.cmd == "import-legacy":
        from runners.snapshot import import_legacy
        print(import_legacy(args.db_dir, args.csv, args.root))
        return
    from runners.load import SnapshotMissing, load
    from runners.report import build
    try:
        snap = load(args.root, args.snapshot)
    except SnapshotMissing as e:
        sys.exit(f"{e}. Сначала снимите данные: python -m runners snapshot")
    for path in build(snap, args.out):
        print(path)
