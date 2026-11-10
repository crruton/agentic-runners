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
python -m runners snapshot            # --only ci_analytics | gitlab | gitlab_pipelines | job_resources

# офлайн, сколько угодно раз: out/report.html (один файл) и out/metrics.json
python -m runners report              # --snapshot 20261109 для конкретного снимка
python -m runners all                 # report + docs/talk/numbers.md + проверка ссылок на показатели

python -m pytest -q tests
```

Числа для доклада берутся из `docs/talk/numbers.md` (генерируется из `out/metrics.json`). Процесс по уровням — `docs/process.md`.

## Тестовый рантайм (`runtime/`)

pytest-плагин, который подключается снаружи проекта, без правок в его репозитории (ADR-0005):

```
PYTHONPATH=runtime PYTEST_ADDOPTS="-p agentic_runtime --rt-workers=auto --rt-cache=/cache/rt --rt-telemetry" pytest
```

- `--rt-workers=N|auto` — воркеры форком от прогретого процесса (Linux): общая память, дешёвый перезапуск; совместим с `PYTEST_XDIST_WORKER`.
- `--rt-cache=DIR` — пропуск файлов тестов, входы которых не менялись с последнего успешного прогона.
- `--rt-telemetry` — строки `job-resources:` для снимка `job_resources`.
- `--rt-select=coverage` — ключ кэша по выполненному коду, а не по импортам через conftest (E09).
- `--rt-fail-fast` — остановка на первом упавшем тесте; с `--rt-cache` прошлые падения выполняются первыми (E07).

Значения только через «=»: иначе pytest может сдвинуть rootdir. Результаты — E04.

## CI-шаблон для проектов

`ci-templates/agentic-ci.yml` — остановка на первой ошибке и полный набор тестов один раз в поезде слияния (ADR-0006). Подключение — `ci-templates/README.md`.

## Для администратора раннеров

`handover/ansible-runners.md` — изменения в Ansible раннеров и тегах с основаниями, проверкой и откатом. В репозиторий раннеров отсюда ничего не пушится.

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
