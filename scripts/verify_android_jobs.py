"""Bind post-upgrade WorkManager ids to post-reboot OS JobScheduler blocks.

The upgrade evidence carries the SystemIdInfo job ids observed after the overwrite install and
first open of the current version. WorkManager schedules every WorkSpec through SystemJobScheduler
on API 23+, so both periodic workers must be visible as persisted JobScheduler jobs after reboot,
before any instrumentation can initialize the application. AlarmManager evidence is not part of
this gate: SystemAlarmService is only enabled by WorkManager below API 23.
"""
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
    blocks = [(int(match.group(1)), match.group(0)) for match in JOB.finditer(dump)]
    verified = {}
    for name in EXPECTED:
        entries = baseline["work"][name]
        assert entries, f"missing post-upgrade WorkManager entries: {name}"
        assert all(entry["scheduler"] == "jobscheduler" for entry in entries), (
            f"post-upgrade scheduler evidence is not JobScheduler: {name}")
        ids = []
        for entry in entries:
            job_id = entry["system_job_id"]
            # JobScheduler ids are per-uid, so the block must also name our package and service.
            candidates = [block for candidate_id, block in blocks if candidate_id == job_id
                          and PACKAGE in block and SERVICE in block]
            assert candidates, f"missing Qingdan SystemJobService job after reboot: {name}/{job_id}"
            ids.append(job_id)
        verified[name] = ids
    result = {"status": "passed", "scope": "after reboot and before post-reboot instrumentation", "jobs": verified}
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
