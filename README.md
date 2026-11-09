# agentic-runners

CI-раннеры под нагрузкой AI-агентов: диагностика, оптимизация и замеры. Здесь же материалы доклада для RULKC Conf 2026.

## Проблема

Агенты запускают тяжёлые пайплайны намного чаще людей: линтеры, сборки, тесты с браузером, подкачка зависимостей, СУБД на каждый тест. За два месяца парк вырос с 4 до 22 раннеров (базовые ноутбуки на 8–16 ядер), а хвост очереди остался: p95 — 24 минуты.

## Отчёт о нагрузке

```
pip install pandas psycopg2-binary requests tabulate pytest

# один раз под VPN: снимок БД ci_analytics и истории GitLab → data/snapshots/<YYYYMMDD>/
export CI_ANALYTICS_DSN=postgresql://exporter:<пароль>@10.71.30.92:30432/ci_analytics
export GITLAB_TOKEN=<токен>
python -m runners snapshot            # --only ci_analytics | gitlab, если нужен один источник

# офлайн, сколько угодно раз: out/report.html (один файл) и out/metrics.json
python -m runners report              # --snapshot 20261109 для конкретного снимка

python -m pytest -q tests
```

Числа для доклада берутся из `out/metrics.json` по идентификатору показателя (например, `queue.p95_s`).

## Где что лежит

| Путь | Что там |
|---|---|
| `runners/` | Снимок источников и отчёт (`python -m runners`) |
| `docs/plan.md` | Пошаговый план на две недели |
| `docs/cfp/application.md` | Заявка на доклад |
| `docs/research/` | Инвентаризация парка, разбор Scion |
| `docs/experiments/` | Журнал экспериментов: только отсюда берутся числа |
| `docs/adr/` | Архитектурные решения |
| `collect/` | Сбор метрик: история CI, PSI, cgroup |
| `bench/` | Воспроизводимые нагрузочные сценарии |
| `openspec/` | Спецификации изменений (OpenSpec) |

## Связанные проекты

- MAH — харнес для AI-агентов: https://github.com/crruton/MAH
