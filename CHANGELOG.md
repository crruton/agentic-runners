# Changelog

## [Unreleased]

### Added

- План проекта, заявка на RULKC Conf 2026, разбор Scion, ADR-0001, каркас OpenSpec.
- Сбор базовой линии: `collect/ci_history.py` (GitLab API), `collect/baseline.py`, `collect/ci_analytics.py` (БД аналитики раннеров).
- E00: базовая линия CI за 30 дней и тренд по неделям (`docs/experiments/E00-*`).
- Пакет `runners`: `python -m runners snapshot` (разовый снимок БД и GitLab без ПДн), `report` (офлайн-отчёт `out/report.html` и `out/metrics.json`), `import-legacy`. Тесты в `tests/`.
- OpenSpec change `runners-report`: спецификации `ci-snapshot` и `runners-report`.
- `runners/whatif.py`: оценка `interruptible` повторным прогоном истории без изменений в репозиториях, модель очереди (E01).
- `docs/research/inventory.md`, `docs/research/fleet.csv`: парк 4 → 22 регистрации за 2 месяца.
- Источники снимка `gitlab_pipelines` (источник пайплайна) и `job_resources` (cgroup v2 задания из логов шардов santinel, только разобранные числа).
- Раздел отчёта «Ресурсы задания: cgroup v2» и E02: естественный эксперимент по классам хостов (8 ядер / 15 GiB против 12 ядер / 31 GiB).
- Команды `runners numbers`, `check`, `all`; `docs/talk/numbers.md`, `docs/process.md`, `docs/research/santinel-ci.md`.
- `runners bench` (с `--resume`): A/B конфигураций ноутбука-раннера на эмулированном хосте (cgroup v2 в Docker Desktop), эмулятор шарда `bench/`, раздел отчёта «Эмуляция A/B», E03.
- ADR-0002 (replay вместо правок в репозиториях), ADR-0003 (телеметрия из логов заданий), ADR-0004 (эмуляция вместо боевого A/B).
- Инвентаризация: эталон ноутбука-раннера (Core Ultra 5 125U, 12 ядер, 39 GiB), сервер `buran` с RTX 4090 вне пула тестов, права на теги раннеров — только у администратора.

### Changed

- Рост в отчёте считается за 2 месяца по полным неделям с усреднением краёв (×3,4 заданий, ×4,9 часов).
- E00: отмены до старта считаются только по GitLab; все запуски CI — через агентов.

### Fixed

- E00: рост больше не считается от неполной первой недели (было ×7,4 / ×16,7 — завышено).
- E01: replay учитывает источник пайплайна; оценка `interruptible` 5,7% вместо завышенных 13,1% (пайплайны по расписанию на main ошибочно считались вытесняемыми).
- CHANGELOG: записи, ошибочно попавшие в раздел Removed, перенесены в Added.

### Removed

- `bench/compact-docker-disk.ps1`: диск сжат напрямую, отдельный скрипт не нужен.

- `collect/snapshot.py`: логика перенесена в `runners/snapshot.py`.
