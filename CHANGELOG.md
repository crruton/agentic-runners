# Changelog

## [Unreleased]

### Added

- План проекта, заявка на RULKC Conf 2026, разбор Scion, ADR-0001, каркас OpenSpec.
- Сбор базовой линии: `collect/ci_history.py` (GitLab API), `collect/baseline.py`, `collect/ci_analytics.py` (БД аналитики раннеров).
- E00: базовая линия CI за 30 дней и тренд за 12 недель (`docs/experiments/E00-*`).
- Пакет `runners`: `python -m runners snapshot` (разовый снимок БД и GitLab без ПДн), `report` (офлайн-отчёт `out/report.html` и `out/metrics.json`), `import-legacy`. Тесты в `tests/`.
- OpenSpec change `runners-report`: спецификации `ci-snapshot` и `runners-report`.

### Changed

- E00: рост считается только по полным неделям (×6,4 заданий, ×9,9 часов вместо ×7,4 и ×16,7); отмены до старта считаются только по GitLab; все запуски CI — через агентов.

### Removed

- `collect/snapshot.py`: логика перенесена в `runners/snapshot.py`.
