import importlib.util
import json
from pathlib import Path
import sys

import pytest


spec = importlib.util.spec_from_file_location('verify_android_jobs',Path(__file__).parents[1]/'scripts'/'verify_android_jobs.py')
jobs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jobs)


def dump(second_package=jobs.PACKAGE,second_service=jobs.SERVICE):
    return f'''JOB #u0a101/11: 11111111
    u0a101 tag=*job*r/{jobs.PACKAGE}/{jobs.SERVICE}
    Source: uid=10101 user=0 pkg={jobs.PACKAGE}
    JobInfo:
      Service: {jobs.PACKAGE}/{jobs.SERVICE}
      Periodic: 900000
  JOB #u0a101/22: 22222222
    u0a101 tag=*job*r/{second_package}/{second_service}
    Source: uid=10101 user=0 pkg={second_package}
    JobInfo:
      Service: {second_package}/{second_service}
      Periodic: 900000
  JOB #u0a108/11: 33333333
    u0a108 tag=*job*r/DownloadManager:com.android.providers.downloads
    Source: uid=1000 user=0 pkg=android
    JobInfo:
      Service: com.android.providers.downloads/.DownloadJobService
  JOB #u0a108/22: 44444444
    u0a108 tag=*job*r/DownloadManager:com.android.providers.downloads
    Source: uid=1000 user=0 pkg=android
    JobInfo:
      Service: com.android.providers.downloads/.DownloadJobService
'''


def baseline(tmp_path,alert_scheduler='jobscheduler'):
    value={'work':{}}
    for name,job_id in [('qingdan-alerts',11),('qingdan-sync',22)]:
        value['work'][name]=[{'id':name+'-uuid','system_job_id':job_id,'state':'ENQUEUED','scheduler':'jobscheduler' if name=='qingdan-sync' else alert_scheduler}]
    path=tmp_path/'baseline.json';path.write_text(json.dumps(value),encoding='utf-8')
    return path


def run(tmp_path,source_text,baseline_text=None):
    source=tmp_path/'jobs.txt';source.write_text(source_text,encoding='utf-8')
    if baseline_text is not None:
        (tmp_path/'baseline.json').write_text(baseline_text,encoding='utf-8')
    output=tmp_path/'verified.json'
    argv=sys.argv;sys.argv=['verify_android_jobs.py',str(tmp_path/'baseline.json'),str(source),str(output)]
    try:jobs.main()
    finally:sys.argv=argv
    return json.loads(output.read_text(encoding='utf-8'))


@pytest.mark.parametrize('prefix',['JOB #','JOB androidx.work:#','JOB androidx.work:'])
def test_both_periodic_workers_bind_to_their_own_system_job_blocks(tmp_path,prefix):
    baseline(tmp_path)
    result=run(tmp_path,dump().replace('JOB #',prefix))
    assert result['status']=='passed'
    assert result['jobs']=={'qingdan-alerts':[11],'qingdan-sync':[22]}


def test_same_numeric_job_id_of_another_uid_cannot_impersonate_the_alert_worker(tmp_path):
    # Job ids are small sequential integers per uid; DownloadManager legitimately owns 11 and 22 too.
    baseline(tmp_path)
    with pytest.raises(AssertionError,match='missing Qingdan SystemJobService job after reboot: qingdan-alerts/11'):
        run(tmp_path,'\n'.join(line for line in dump().splitlines() if 'u0a101' not in line and jobs.PACKAGE not in line))


def test_unrelated_job_cannot_impersonate_the_sync_worker(tmp_path):
    baseline(tmp_path)
    with pytest.raises(AssertionError,match='missing Qingdan SystemJobService job after reboot: qingdan-sync/22'):
        run(tmp_path,dump(second_service='com.example.OtherService'))


def test_alarm_scheduler_evidence_is_rejected_for_both_workers(tmp_path):
    baseline(tmp_path,alert_scheduler='alarm')
    with pytest.raises(AssertionError,match='post-upgrade scheduler evidence is not JobScheduler: qingdan-alerts'):
        run(tmp_path,dump())
