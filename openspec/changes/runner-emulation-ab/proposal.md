# Proposal

## Why

ADR-0001 требует A/B конфигураций раннера, но менять боевые раннеры нельзя (решение владельца 09.11), а Grafana недоступна. Без A/B доклад остаётся на корреляциях E02.

## What Changes

- Команда `runners bench`: A/B конфигураций ноутбука-раннера на эмулированном хосте (cgroup v2 в VM Docker Desktop) с одинаковой нагрузкой во всех сценариях.
- Эмулятор шарда `bench/shard_emu.py`, откалиброванный по замерам santinel. Проверка: он воспроизводит OOM на старом бюджете памяти.
- Раздел отчёта «Эмуляция A/B» и показатели `bench.*`, E03 в журнале экспериментов.
- ADR-0002…0004: replay вместо правок в репозиториях, телеметрия из логов, эмуляция вместо боевого A/B.

## Capabilities

### New Capabilities
- `runner-emulation`: воспроизводимый A/B конфигураций раннера на эмулированном хосте.

### Modified Capabilities
- нет

## Impact

- Код: `bench/`, `runners/bench.py`, `runners/metrics.py`, `runners/load.py`, `runners/report.py`, `runners/docs.py`, `runners/cli.py`, тесты.
- Требования к запуску: Docker Desktop с cgroup v2; около 6 ГиБ свободной памяти VM на сценарий; привилегированный вспомогательный контейнер для настройки cgroup.
