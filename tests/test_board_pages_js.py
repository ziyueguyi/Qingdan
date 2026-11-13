import shutil
import subprocess
from pathlib import Path

import pytest


def test_board_pager_behavior(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is required for frontend behavior tests')
    root = Path(__file__).resolve().parents[1]
    for name in ('task_pages','board_pages'):
        source = (root / f'src/qingdan/web/{name}.js').read_text(encoding='utf-8')
        (tmp_path / f'{name}.mjs').write_text(source.replace('./task_pages.js','./task_pages.mjs'),encoding='utf-8')
    result = subprocess.run([node,str(root/'tests/board_pages.mjs'),(tmp_path/'board_pages.mjs').as_uri()],
                            capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'refresh races passed' in result.stdout
