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
    {jobs.PACKAGE}
    Service: {jobs.PACKAGE}/{jobs.SERVICE}
    period=900000
  JOB #u0a101/22: 22222222
    {second_package}
    Service: {second_package}/{second_service}
    period=900000
'''


def baseline(tmp_path):
    value={'work':{}}
    for name,job_id in [('qingdan-alerts',11),('qingdan-sync',22)]:
        value['work'][name]=[{'id':name+'-uuid','system_job_id':job_id,'state':'ENQUEUED'}]
    path=tmp_path/'baseline.json';path.write_text(json.dumps(value),encoding='utf-8')
    return path


@pytest.mark.parametrize('prefix',['JOB #','JOB androidx.work:#','JOB androidx.work:'])
def test_two_periodic_workers_are_bound_to_their_os_job_blocks(tmp_path,monkeypatch,prefix):
    source=tmp_path/'jobs.txt';source.write_text(dump().replace('JOB #',prefix),encoding='utf-8')
    output=tmp_path/'verified.json'
    monkeypatch.setattr(sys,'argv',['verify_android_jobs.py',str(baseline(tmp_path)),str(source),str(output)])
    jobs.main()
    result=json.loads(output.read_text(encoding='utf-8'))
    assert result['status']=='passed'
    assert result['jobs']=={'qingdan-alerts':[11],'qingdan-sync':[22]}


def test_unrelated_job_cannot_impersonate_the_sync_worker(tmp_path,monkeypatch):
    source=tmp_path/'jobs.txt';source.write_text(dump(second_service='com.example.OtherService'),encoding='utf-8')
    monkeypatch.setattr(sys,'argv',['verify_android_jobs.py',str(baseline(tmp_path)),str(source),str(tmp_path/'verified.json')])
    with pytest.raises(AssertionError,match='wrong OS job block'):
        jobs.main()
