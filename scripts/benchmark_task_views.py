"""Reproducible isolated 10k-task query benchmark; never opens the user DB.

Run: python scripts/benchmark_task_views.py --output buildtmp/task-views.json
"""
from __future__ import annotations

import argparse
import gc
import json
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import qingdan.bootstrap  # noqa: E402,F401
from qingdan.core import view  # noqa: E402
from qingdan.core.context import init  # noqa: E402
from qingdan.core.registry import call  # noqa: E402


def measure(function, repeat):
    elapsed = []
    for _ in range(repeat):
        started = time.perf_counter()
        result = function()
        elapsed.append((time.perf_counter() - started) * 1000)
    gc.collect()
    tracemalloc.start()
    function()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return result, {'median_ms': round(statistics.median(elapsed), 3),
                    'max_ms': round(max(elapsed), 3), 'peak_python_bytes': peak}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tasks', type=int, default=10_000)
    parser.add_argument('--repeat', type=int, default=5)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.tasks < 100 or args.repeat < 1:
        parser.error('tasks >= 100, repeat >= 1')
    with tempfile.TemporaryDirectory(prefix='qingdan-view-benchmark-') as directory:
        store = init(directory, device_id='D-benchmark').store
        rows = []
        for i in range(args.tasks):
            data = {'title': f'Task {i:06}', 'note': 'Benchmark only', 'priority': 1+i % 4,
                    'status': ['todo', 'doing', 'done', 'cancelled'][i % 4],
                    'due_at': f'2026-11-{1+i % 28:02}T09:00:00' if i % 9 else None,
                    'created_at': '2026-11-01T09:00:00', 'done_at': '2026-11-13T09:00:00',
                    'list_id': f'L{i % 20}', 'tags': [f'Tag{i % 20}'], 'order': i % 10}
            rows.append((f'T{i:08}', i, json.dumps(data)))
        store.db.executemany("INSERT INTO item(id,kind,rev_ms,rev_seq,rev_dev,data,updated_at) "
                             "VALUES(?,'task',?,0,'D-benchmark',?,1000)", rows)
        store.db.commit()
        del rows
        today = '2026-11-13'
        def legacy():
            pool = store.list_items(kind='task', include_deleted=True)
            return view.sort_items(view.apply_view('all', pool, today), 'smart')
        old, old_metrics = measure(legacy, args.repeat)
        expected = [it.id for it in old]
        del old
        first, first_metrics = measure(lambda: call('view.query', name='all', today=today, limit=50), args.repeat)
        middle, middle_metrics = measure(lambda: call('view.query', name='all', today=today, limit=50,
                                                      offset=args.tasks // 2), args.repeat)
        counts, counts_metrics = measure(lambda: call('view.counts', today=today), args.repeat)
        stats, stats_metrics = measure(lambda: call('stats.today', today=today), args.repeat)
        assert [it['id'] for it in first['items']] == expected[:50]
        assert [it['id'] for it in middle['items']] == expected[args.tasks // 2:args.tasks // 2+50]
        assert counts['all'] == first['total'] == args.tasks
        assert stats['open_total'] == args.tasks // 4 * 2 + min(args.tasks % 4, 2)
        report = {'tasks': args.tasks, 'repeat': args.repeat, 'python': platform.python_version(),
                  'platform': platform.platform(), 'page_size': 50,
                  'legacy_materialize_sort': old_metrics, 'first_page': first_metrics,
                  'middle_page': middle_metrics, 'counts': counts_metrics, 'stats': stats_metrics,
                  'first_page_json_bytes': len(json.dumps(first).encode('utf-8')),
                  'scope': 'Backend queries only; frontend rendering and Android performance not measured.'}
        store.db.close()
        store._local.conn = None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
