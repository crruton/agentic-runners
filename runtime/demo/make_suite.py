"""Демо-набор для E04: тяжёлые импорты и общие данные модуля, как у проектов на SQLAlchemy/pandas.

python make_suite.py OUT_DIR [files] [tests_per_file]
Создаёт пакет demo_app (импорт pandas, numpy, sqlalchemy и около 250 MiB данных модуля) и файлы
тестов, каждый из которых читает часть общих данных и немного считает.
"""

import os
import sys

APP = '''
import numpy as np
import pandas as pd
import sqlalchemy as sa

# Справочники уровня модуля, как кэши ORM/метаданных в реальных проектах
DATA = {i: ("payload-" * 12) + str(i) for i in range(1_500_000)}
FRAME = pd.DataFrame({"a": np.arange(2_000_000), "b": np.random.default_rng(0).random(2_000_000)})
META = sa.MetaData()
TABLES = [sa.Table(f"t{i}", META, *[sa.Column(f"c{j}", sa.Integer) for j in range(20)]) for i in range(300)]
'''

TEST = '''
import time
import demo_app

def _work(k):
    s = sum(len(demo_app.DATA[i]) for i in range(k * 30_000, k * 30_000 + 30_000))
    s += float(demo_app.FRAME["b"].iloc[k * 1000:(k + 1) * 1000].sum())
    t = time.perf_counter() + 0.05
    while time.perf_counter() < t:
        pass
    return s

{tests}
'''


def main(out, files=24, per_file=8):
    os.makedirs(os.path.join(out, "demo_app"), exist_ok=True)
    with open(os.path.join(out, "demo_app", "__init__.py"), "w", encoding="utf-8") as f:
        f.write(APP)
    for n in range(files):
        tests = "\n".join(f"def test_{n}_{k}():\n    assert _work({(n * per_file + k) % 40}) > 0\n" for k in range(per_file))
        with open(os.path.join(out, f"test_mod{n:02d}.py"), "w", encoding="utf-8") as f:
            f.write(TEST.format(tests=tests))
    with open(os.path.join(out, "pytest.ini"), "w", encoding="utf-8") as f:
        f.write("[pytest]\naddopts = -p no:cacheprovider\n")


if __name__ == "__main__":
    main(sys.argv[1], *(int(a) for a in sys.argv[2:]))
