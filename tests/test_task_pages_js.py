import shutil
import subprocess
from pathlib import Path

import pytest


def test_task_pager_behavior(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for frontend behavior tests')
    root = Path(__file__).resolve().parents[1]
    module = tmp_path / 'task_pages.mjs'
    module.write_text((root / 'src/qingdan/web/task_pages.js').read_text(encoding='utf-8'), encoding='utf-8')
    result = subprocess.run([node, str(root / 'tests/task_pages.mjs'), module.as_uri()],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'prefetch failure can be retried' in result.stdout
