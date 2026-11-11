"""Exercise every registered operation through the actual CLI dispatcher."""
import json
from fastapi.testclient import TestClient
from qingdan.api.http import build_app
from qingdan.cli import main
from qingdan.core import context
from qingdan.core.alerting.base import CHANNELS, register_channel
from qingdan.core.registry import REGISTRY
from test_contract import _make_prerequisites, _required_raw, _target_key, _FakeChannel, EXTRA


def test_every_feature_is_reachable_from_cli(tmp_path,capsys):
    failures=[]
    original=dict(CHANNELS)
    try:
        for cname in ('toast','dingtalk','email'):
            register_channel(_FakeChannel(cname))
        for name,f in list(REGISTRY.items()):
            data_dir=tmp_path/name.replace('.','_')
            context.init(data_dir,device_id='D-cli-contract')
            with TestClient(build_app()) as client:
                ids=_make_prerequisites(client,name.replace('.','_'))
            path=f.http[1]
            target=ids[_target_key(path)]
            raw=_required_raw(f,ids,target,{})
            raw.update(EXTRA.get(name,{}))
            if '<section>' in str(raw.get('order')):
                raw['order']=[ids['section']]
            for key,value in list(raw.items()):
                if value=='<channel>': raw[key]=ids['channel']
            if '{item_id}' in path: raw['item_id']=target
            if '{name}' in path:
                raw['name']=ids['backup_name'] if '/backups/' in path else ids['channel'] if '/alert/channels/' in path else ids['view_name']
            argv=['--data-dir',str(data_dir),'call',name]
            for key,value in raw.items():
                argv.extend(['--'+key.replace('_','-'),json.dumps(value,ensure_ascii=False)])
            status=main(argv)
            output=capsys.readouterr()
            if status:
                error=json.loads(output.err)['error']
                if not (f.needs_auth and error['code']=='AUTH_REQUIRED'):
                    failures.append((name,status,error))
            else:
                json.loads(output.out)
        assert not failures,failures
    finally:
        CHANNELS.clear();CHANNELS.update(original)


def test_standard_cli_preserves_business_group_parameter(app_ctx,capsys):
    assert main(['--data-dir',str(app_ctx.data_dir),'view','query','--name','all','--group','priority'])==0
    result=json.loads(capsys.readouterr().out)
    # A nonempty dataset makes the grouping behavior observable.
    from qingdan.core.registry import call
    call('task.create',title='P1',priority=1)
    assert main(['--data-dir',str(app_ctx.data_dir),'view','query','--name','all','--group','priority'])==0
    result=json.loads(capsys.readouterr().out)
    assert result['groups'][0]['key']=='prio_1'

