# Tasks

## 1. Каркас пакета

- [x] 1.1 `runners/__init__.py`, `runners/__main__.py`, `runners/cli.py` с подкомандами `snapshot`, `import-legacy`, `report`
- [x] 1.2 `collect/__init__.py`, чтобы импортировать `collect.ci_history`; `out/` в `.gitignore`

## 2. Снимок (ci-snapshot)

- [x] 2.1 `runners/snapshot.py`: выгрузка `ci_analytics` (таблицы и представления, read-only, запись через `*.part`)
- [x] 2.2 Выгрузка GitLab через `collect.ci_history` → `gitlab_jobs.csv.gz` без `user`/`commit_title`, с `author_hash`
- [x] 2.3 `manifest.json`: время, строки по таблицам, параметры GitLab, статус источников; ненулевой код при `failed`
- [x] 2.4 `import-legacy`: перенос `data/ci_analytics/20261109/` и `data/jobs_30d.csv` в `data/snapshots/20261109/`
- [x] 2.5 Удалить `collect/snapshot.py` (логика перешла в пакет)

## 3. Метрики

- [x] 3.1 `runners/load.py`: загрузка снимка в DataFrame, выбор последнего снимка
- [x] 3.2 `runners/metrics.py`: KPI, тренд по неделям, очередь по часам и дням, отменённые до старта, классы, топ проектов, разбор главного проекта, раннеры, качество данных
- [x] 3.3 Тесты метрик на маленьком синтетическом снимке (генерируется в `tests/conftest.py`)

## 4. Отчёт (runners-report)

- [x] 4.1 `runners/charts.py`: SVG вертикальные и горизонтальные столбцы (линия не понадобилась), цвета через CSS-переменные
- [x] 4.2 `runners/report.py`: HTML с девятью разделами и подписями «источник · период», `metrics.json` с детерминированной сериализацией
- [x] 4.3 Тесты: нет внешних ссылок, нет логинов, все разделы на месте, повторный запуск даёт идентичный JSON, ошибка без снимка

## 5. Проверка и фиксация

- [x] 5.1 `import-legacy` на реальных данных, `report` офлайн; сверить KPI с `docs/experiments/E00-baseline-summary.md`
- [x] 5.2 Обновить E00 (все запуски через агентов; ссылка на отчёт), README (как запустить), CHANGELOG
- [x] 5.3 `openspec validate runners-report`, коммит, push в обе удалённые ветки
