# Proposal

## Why

Grafana недоступна, а репозитории проектов менять нельзя (09.11). Первая версия E01 завысила эффект `interruptible` (13,1% вместо 5,7%), потому что не знала источник пайплайна. Ресурсы задания на уровне ядра (cgroup v2) уже печатаются в логах шардов santinel, но в снимок не попадали. А цифры в документах никак не проверялись на соответствие данным.

## What Changes

- Снимок получает два источника: пайплайны GitLab с источником (`gitlab_pipelines`) и разобранные строки `job-resources:` из логов шардов santinel (`job_resources`; только начало и конец лога, сырой лог не хранится).
- Replay `interruptible` учитывает источник пайплайна: вытесняют и вытесняются только push и MR.
- Новый раздел отчёта «Ресурсы задания: cgroup v2».
- Команды `numbers` (docs/talk/numbers.md из metrics.json), `check` (ссылки на показатели в docs существуют, numbers.md свежий) и `all` (report + numbers + check).

## Capabilities

### New Capabilities
- `docs-traceability`: связь цифр в документах и слайдах с показателями отчёта.

### Modified Capabilities
- `ci-snapshot`: два новых источника.
- `runners-report`: replay с источником пайплайна, раздел cgroup.

## Impact

- Код: `runners/resources.py`, `runners/docs.py`, изменения в `snapshot.py`, `load.py`, `metrics.py`, `whatif.py`, `cli.py`, тесты.
- Документы: `docs/process.md`, `docs/research/santinel-ci.md`, `docs/talk/numbers.md`, исправление E01.
- Нагрузка на GitLab: около 8 тыс. Range-запросов к логам при снятии снимка.
