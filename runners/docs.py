"""Связь документов с данными: таблица цифр для доклада и проверка ссылок на показатели.

- `numbers`: docs/talk/numbers.md генерируется из out/metrics.json. Слайды берут цифры только оттуда.
- `check`: каждый идентификатор показателя в `обратных кавычках` в docs/**/*.md существует в metrics.json,
  а docs/talk/numbers.md совпадает с тем, что дал бы `numbers` сейчас.
"""

import glob
import json
import os
import re

from runners.metrics import Metric
from runners.report import fmt_value

PREFIXES = ("trend", "queue", "canceled", "classes", "projects", "hot", "runners", "whatif", "cgroup", "bench", "quality")
REF = re.compile(r"`((?:%s)\.[a-z0-9_-]+)`" % "|".join(PREFIXES))
TITLES = {"trend": "Рост", "queue": "Очередь", "canceled": "Отмены до старта", "classes": "Классы заданий",
          "projects": "Проекты", "hot": "Главный проект", "runners": "Парк раннеров",
          "cgroup": "Ресурсы задания (cgroup)", "bench": "Эмуляция A/B", "whatif": "Что если (replay)", "quality": "Качество данных"}


def _metrics(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def render_numbers(doc):
    ms = doc["metrics"]
    lines = ["# Цифры для доклада", "",
             f"Сгенерировано `python -m runners numbers` из `out/metrics.json`, снимок {doc['snapshot']}. "
             "Руками не править: перегенерировать после `runners report`.", ""]
    for prefix in PREFIXES:
        ids = sorted(k for k in ms if k.split(".")[0] == prefix)
        if not ids:
            continue
        lines += [f"## {TITLES[prefix]}", "", "| Показатель | Значение | Источник · период | id |", "|---|---|---|---|"]
        for k in ids:
            m = ms[k]
            v = fmt_value(Metric(k, m["label"], m["value"], m["unit"], m["source"], m["period"]))
            lines.append(f"| {m['label']} | {v} | {m['source']} · {m['period']} | `{k}` |")
        lines.append("")
    return "\n".join(lines)


def numbers(metrics="out/metrics.json", out="docs/talk/numbers.md"):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(render_numbers(_metrics(metrics)))
    return out


def check(metrics="out/metrics.json", docs="docs", numbers_md="docs/talk/numbers.md"):
    """Список проблем; пустой — всё сходится."""
    doc = _metrics(metrics)
    known = set(doc["metrics"])
    problems = []
    for path in sorted(glob.glob(os.path.join(docs, "**", "*.md"), recursive=True)):
        with open(path, encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                for ref in REF.findall(line):
                    if ref not in known:
                        problems.append(f"{path}:{n}: нет показателя `{ref}` в {metrics}")
    if os.path.exists(numbers_md):
        with open(numbers_md, encoding="utf-8") as f:
            if f.read() != render_numbers(doc):
                problems.append(f"{numbers_md}: устарел, выполните python -m runners numbers")
    else:
        problems.append(f"{numbers_md}: нет файла, выполните python -m runners numbers")
    return problems
