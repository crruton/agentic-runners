"""CLI: python -m runners {snapshot,import-legacy,report}."""

import argparse
import sys


def main(argv=None):
    ap = argparse.ArgumentParser(prog="runners", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="снять БД ci_analytics и историю GitLab в data/snapshots/<дата>/")
    s.add_argument("--root", default="data/snapshots")
    s.add_argument("--days", type=int, default=30, help="глубина выгрузки GitLab")
    s.add_argument("--only", choices=["ci_analytics", "gitlab"])

    li = sub.add_parser("import-legacy", help="перенести старый снимок и CSV в новую структуру")
    li.add_argument("--db-dir", default="data/ci_analytics/20261109")
    li.add_argument("--csv", default="data/jobs_30d.csv")
    li.add_argument("--root", default="data/snapshots")

    r = sub.add_parser("report", help="построить out/report.html и out/metrics.json по снимку (офлайн)")
    r.add_argument("--root", default="data/snapshots")
    r.add_argument("--snapshot", help="YYYYMMDD, по умолчанию последний")
    r.add_argument("--out", default="out")

    args = ap.parse_args(argv)
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
