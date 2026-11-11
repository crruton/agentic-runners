# ADR

| ID | Статус | Суть | Applies To |
|---|---|---|---|
| [ADR-0001](ADR-0001-measure-first.md) | APPROVED | Сначала базовая линия, затем одно изменение за раз в A/B | `collect/**`, `bench/**`, `docs/experiments/**` |
| [ADR-0002](ADR-0002-replay-not-repo-changes.md) | APPROVED | Политики пайплайнов оцениваем replay истории, репозитории проектов не меняем | `runners/whatif.py`, `docs/experiments/**` |
| [ADR-0003](ADR-0003-telemetry-from-job-logs.md) | APPROVED | Телеметрия ядра из строк `job-resources:` в логах заданий вместо Grafana | `runners/resources.py`, `runners/snapshot.py`, `docs/experiments/**` |
| [ADR-0004](ADR-0004-emulated-runner-ab.md) | APPROVED | A/B конфигураций раннера на эмулированном ноутбуке (cgroup v2 в Docker Desktop) | `bench/**`, `runners/bench.py`, `docs/experiments/E03-*` |
| [ADR-0005](ADR-0005-platform-test-runtime.md) | APPROVED | Раннеры разгружаем тестовым рантаймом (форк от прогретого процесса, кэш результатов, телеметрия), а не размером раннеров | `runtime/**`, `docs/experiments/E04-*` |
| [ADR-0006](ADR-0006-rollout-order.md) | APPROVED | Порядок внедрения: H1 → харнес (H2, H9, H11) → H10; пакетирование и PSI-допуск отложены | `docs/plan.md`, `docs/research/agentic-ci-hypotheses.md` |
| [ADR-0007](ADR-0007-agent-attestation.md) | APPROVED | CI принимает подписанное свидетельство проверки агента вместо повторной проверки; аудит 10% | `runtime/agentic_runtime/attest.py`, `ci-templates/agentic-attest.yml`, `skills/ci-preflight/**` |
