#!/bin/sh
# E04 внутри контейнера с лимитами слота. Вариант задаёт MODE (xdist | runtime | cache | partial | ff_base | ff_first | ff_second), воркеров — N.
# Каждый вариант запускается в своём контейнере, чтобы memory.peak cgroup относился только к нему.
set -e
python /work/runtime/demo/make_suite.py /tmp/suite 24 8
cd /tmp/suite
export PYTHONPATH=/work/runtime
now() { python -c 'import time; print(time.time())'; }
case "$MODE" in
  xdist)   s=$(now); python -m pytest -q -n "$N" > /tmp/out.txt 2>&1 || true ;;
  runtime) s=$(now); python -m pytest -q -p agentic_runtime --rt-workers "$N" > /tmp/out.txt 2>&1 || true ;;
  cache)   python -m pytest -q -p agentic_runtime --rt-workers "$N" --rt-cache=/tmp/rt > /tmp/warm.txt 2>&1 || true
           s=$(now); python -m pytest -q -p agentic_runtime --rt-workers "$N" --rt-cache=/tmp/rt > /tmp/out.txt 2>&1 || true ;;
  partial) python -m pytest -q -p agentic_runtime --rt-workers "$N" --rt-cache=/tmp/rt > /tmp/warm.txt 2>&1 || true
           echo "# changed" >> /tmp/suite/test_mod00.py
           s=$(now); python -m pytest -q -p agentic_runtime --rt-workers "$N" --rt-cache=/tmp/rt > /tmp/out.txt 2>&1 || true ;;
  ff_base|ff_first|ff_second)
           # H1 (E07): падение в последнем файле набора
           printf '
def test_broken():
    assert False
' >> /tmp/suite/test_mod23.py
           FF=""; [ "$MODE" != ff_base ] && FF="--rt-fail-fast --rt-cache=/tmp/rt"
           [ "$MODE" = ff_second ] && python -m pytest -q -p agentic_runtime --rt-workers "$N" $FF > /tmp/warm.txt 2>&1 || true
           s=$(now); python -m pytest -q -p agentic_runtime --rt-workers "$N" $FF > /tmp/out.txt 2>&1 || true ;;
  cov_static|cov_record)
           # E09: накладные расходы записи покрытия (первый прогон, кэш пуст)
           SEL=static; [ "$MODE" = cov_record ] && SEL=coverage
           s=$(now); python -m pytest -q -p agentic_runtime --rt-workers "$N" --rt-cache=/tmp/rt --rt-select=$SEL > /tmp/out.txt 2>&1 || true ;;
esac
e=$(now)
echo "summary: $(tail -1 /tmp/out.txt)"
grep "по кэшу" /tmp/out.txt || true
python -c "print('wall_s:', round($e - $s, 1))"
echo "mem_peak_mib: $(( $(cat /sys/fs/cgroup/memory.peak) / 1048576 ))"
echo "events: $(grep -E '^(oom_kill|max) ' /sys/fs/cgroup/memory.events | tr '\n' ' ')"
