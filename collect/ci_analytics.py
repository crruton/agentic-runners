"""Базовая линия из БД аналитики раннеров (ci_analytics): тренд за 90 дней и нагрузка по раннерам.

Дополняет baseline.py: в БД дневные агрегаты с 11.08 и детализация заданий за 15 дней.
Окружение: CI_ANALYTICS_DSN, например postgresql://exporter:***@10.71.30.92:30432/ci_analytics
Запуск: python collect/ci_analytics.py > data/E00-ci-analytics.md
"""

import os
import sys

import psycopg2

QUERIES = [
    ("Неделя к неделе (все проекты, scope 1)", """
        select date_trunc('week', day)::date as week, sum(attempts) as jobs,
          round(sum(runner_hours)::numeric, 1) as runner_h,
          round((sum(queue_sum) / nullif(sum(queue_count), 0))::numeric, 1) as avg_queue_s,
          round((sum(duration_sum) / nullif(sum(duration_count), 0))::numeric, 1) as avg_dur_s,
          round((100.0 * sum(attempts) filter (where outcome <> 'success') / sum(attempts))::numeric, 1) as not_success_pct
        from project_daily_report group by 1 order by 1"""),
    ("Топ проектов по часам раннеров, 30 дней", """
        select path, sum(attempts) as jobs, round(sum(runner_hours)::numeric, 1) as runner_h,
          round((100.0 * sum(runner_hours) / sum(sum(runner_hours)) over ())::numeric, 1) as share_pct,
          round((sum(queue_sum) / nullif(sum(queue_count), 0))::numeric, 1) as avg_queue_s
        from project_daily_report where day > current_date - 30
        group by 1 order by 3 desc limit 10"""),
    ("По раннерам, детализация за 15 дней", """
        select coalesce(j.data->'runner'->>'id', '-') as runner_id,
          coalesce(r.data->>'tag_list', '') as tags, count(*) as jobs,
          round(avg((j.data->>'queued_duration')::float)::numeric, 0) as avg_queue_s,
          round((percentile_cont(0.95) within group (order by (j.data->>'queued_duration')::float))::numeric, 0) as p95_queue_s,
          round((sum((j.data->>'duration')::float) / 3600)::numeric, 1) as busy_h
        from job_details j left join runners r on r.id = (j.data->'runner'->>'id')::bigint
        group by 1, 2 order by busy_h desc nulls last"""),
    ("Задания без раннера, 15 дней", """
        select data->>'status' as status, coalesce(nullif(data->>'failure_reason', ''), '-') as failure_reason,
          count(*) as jobs,
          round(avg((data->>'queued_duration')::float)::numeric, 0) as avg_queue_s
        from job_details where data->'runner' is null or jsonb_typeof(data->'runner') <> 'object'
        group by 1, 2 order by 3 desc"""),
]


def table(cur):
    cols = [d[0] for d in cur.description]
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for row in cur.fetchall():
        lines.append("| " + " | ".join("" if v is None else str(v) for v in row) + " |")
    return "\n".join(lines)


def main():
    dsn = os.environ.get("CI_ANALYTICS_DSN")
    if not dsn:
        sys.exit("CI_ANALYTICS_DSN не задан")
    conn = psycopg2.connect(dsn, connect_timeout=20)
    conn.set_session(readonly=True)
    cur = conn.cursor()
    print("# E00. Базовая линия из ci_analytics\n")
    cur.execute("select min(day), max(day) from project_daily_report")
    print("- Агрегаты: {} — {}".format(*cur.fetchone()))
    cur.execute("select min(finished_at)::date, max(finished_at)::date, count(*) from job_details")
    print("- Детализация заданий: {} — {}, заданий: {}".format(*cur.fetchone()))
    cur.execute("select count(*), count(*) filter (where (data->>'online')::bool) from runners")
    print("- Раннеров зарегистрировано: {}, онлайн: {}\n".format(*cur.fetchone()))
    for title, sql in QUERIES:
        cur.execute(sql)
        print(f"## {title}\n\n{table(cur)}\n")


if __name__ == "__main__":
    main()
