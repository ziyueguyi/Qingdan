"""Bind baseline WorkManager IDs to distinct post-reboot OS JobScheduler blocks."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

PACKAGE = "com.ziyueguyi.qingdan"
SERVICE = "androidx.work.impl.background.systemjob.SystemJobService"
EXPECTED = ("qingdan-alerts", "qingdan-sync")
JOB = re.compile(r"(?ms)^[ \t]*JOB\s+(?:[A-Za-z0-9_.-]+:)?#?\S*/(\d+):.*?(?=^[ \t]*JOB\s|\Z)")


def main() -> None:
    baseline_path, dump_path, output_path = map(Path, sys.argv[1:4])
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    dump = dump_path.read_text(encoding="utf-8")
    blocks = {int(match.group(1)): match.group(0) for match in JOB.finditer(dump)}
    verified = {}
    for name in EXPECTED:
        entries = baseline["work"][name]
        assert entries, f"missing baseline WorkManager entries: {name}"
        ids = []
        for entry in entries:
            job_id = entry["system_job_id"]
            block = blocks.get(job_id)
            assert block is not None, f"missing OS job after reboot: {name}/{job_id}"
            assert PACKAGE in block and SERVICE in block, f"wrong OS job block: {name}/{job_id}: {block}"
            ids.append(job_id)
        verified[name] = ids
    result = {"status": "passed", "scope": "after reboot and before post-reboot instrumentation", "jobs": verified}
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
