# Proposal

## Why

Гипотез 13, а проверять на проде можно только после прав и пилотов. Нужна воспроизводимая офлайн-оценка, чтобы выбрать порядок внедрения по данным (ADR-0006).

## What Changes

- `runners/hypotheses.py`: оценка H1, H2, H3, H10, H12, H13 по снимку, раздел отчёта «Гипотезы», показатели `hyp.*`.
- Загрузчик заданий БД получает `pipeline_id`, `started_at`, `allow_failure`.
- Сценарий эмуляции `P-psi` для H6.

## Capabilities

### New Capabilities
- `hypotheses-replay`: офлайн-оценка гипотез о CI по истории снимка.

### Modified Capabilities
- нет

## Impact

- Код: `runners/hypotheses.py`, `runners/load.py`, `runners/bench.py`, отчёт, тесты.
