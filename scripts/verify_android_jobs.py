"""Bind baseline work to post-reboot OS JobScheduler and AlarmManager evidence."""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

PACKAGE = "com.ziyueguyi.qingdan"
SERVICE = "androidx.work.impl.background.systemjob.SystemJobService"
ALARM_SERVICE = "androidx.work.impl.background.systemalarm.SystemAlarmService"
EXPECTED = ("qingdan-alerts", "qingdan-sync")
JOB = re.compile(r"(?ms)^[ \t]*JOB\s+(?:[A-Za-z0-9_.-]+:)?#?\S*/(\d+):.*?(?=^[ \t]*JOB\s|\Z)")
ALARM = re.compile(r"(?s)Alarm\{.*?(?=Alarm\{|\Z)")


def main() -> None:
    baseline_path, dump_path, alarm_path, output_path = map(Path, sys.argv[1:5])
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    dump = dump_path.read_text(encoding="utf-8")
    alarm_dump = alarm_path.read_text(encoding="utf-8")
    blocks = [(int(match.group(1)), match.group(0)) for match in JOB.finditer(dump)]
    alarm_blocks = [match.group(0) for match in ALARM.finditer(alarm_dump)]
    verified = {}
    for name in EXPECTED:
        entries = baseline["work"][name]
        assert entries, f"missing baseline WorkManager entries: {name}"
        ids = []
        for entry in entries:
            job_id = entry["system_job_id"]
            if name == "qingdan-sync":
                candidates = [block for candidate_id, block in blocks if candidate_id == job_id
                              and PACKAGE in block and SERVICE in block]
            else:
                candidates = [block for block in alarm_blocks if PACKAGE in block and ALARM_SERVICE in block]
            assert candidates, f"missing Qingdan OS schedule after reboot: {name}/{job_id}"
            ids.append(job_id)
        verified[name] = ids
    result = {"status": "passed", "scope": "after reboot and before post-reboot instrumentation", "jobs": verified}
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
