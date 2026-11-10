# Раннеры по их Ansible-конфигурации

Источник: `sys-admins/ansible/ansible-notebook-gitlab-runner`, ветка `main`, прочитано через GitLab API 10.11.2026, только чтение. Файлы `vault.yml` не читались. Копии — `data/evidence/ansible-runners/` (не в git). Изменения в этот репозиторий вносятся через MR и ручной deploy с защищённой ветки (`check` → `deploy`); у нас доступ Developer.

## Как устроен парк

| Хосты | Ядра | Слоты | Задание | Каждый сервис | CPU задания и сервисов |
|---|---|---|---|---|---|
| notebook-runner-1…6 | 4 физических / 8 потоков | 2 (`concurrent: 2`) | `memory: 4g`, без свопа | `service_memory: 2g` | общие 2 физических ядра на слот |
| notebook-runner-7 | 6 / 12 | 3 | 8g, 8g, 6g | 2g | 2 физических ядра на слот |
| notebook-runner-8 | 6 / 12 | 3 | 5g × 3 | 2g | 2 физических ядра на слот |
| adata-runner | 8 потоков | 2 | 4608m | 2g | 4 потока на слот |
| nvme-runner | 4 потока | 2 | 4608m | 2g | 1 физическое ядро на слот |
| runner-ssd | 4 ядра без HT | 2 | 3g | 1536m | 2 ядра на слот |

Общее для всех (`group_vars/gitlab_runners`): Docker executor, `privileged: true`, проброшен `/var/run/docker.sock`, `pull_policy: if-not-present`, зеркало Docker Hub `mirror.gcr.io`, общий S3-кэш раннеров (MinIO), node_exporter и метрики раннера для Prometheus, ежечасная очистка Docker при свободном месте < 25%.

## Что это объясняет

1. **OOM хоста на 8-поточных ноутбуках — переподписка памяти.** README репозитория задаёт бюджет `(memory + service_memory) × concurrent ≤ RAM − 2 GB`, то есть считает один сервис. У santinel их два (Postgres и Redis): слот требует 4 + 2 + 2 = 8 GiB, два слота — 16 GiB при `MemTotal` около 15 GiB. Отсюда `oom_kill` хоста до 51 (E02). Исправление предыдущей версии: сервисы задания не «вне лимита», у каждого свой лимит 2g; проблема в сумме лимитов.
2. **Тесты и их БД делят одни и те же 2 физических ядра.** `service_cpuset_cpus` совпадает с `cpuset_cpus`: Postgres конкурирует с xdist-воркерами за ядра слота.
3. **`pull_policy` уже `if-not-present`.** Рычаг «образ скачивается в каждом задании» к этим раннерам не относится (замер +43 с у santinel — на его собственном раннере 40).
4. **`docker build` через `docker.sock` не попадает под лимиты**: он выполняется dockerd хоста.

## Где менять

Все лимиты — в `host_vars/<host>/vars.yml` (`gitlab_runner_list[].docker`), число слотов — в `gitlab_runner_global_config_override.concurrent`. Изменение конфига перезапускает `gitlab-runner` и прерывает идущие задания — выкатывать вне пиков и по одному хосту (`ANSIBLE_LIMIT`).
