"""Smoke-check a frozen release using isolated temporary data only."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request
import socket
import ssl
import urllib.error


def verify(release: Path) -> None:
    release = release.resolve()
    project = Path(__file__).resolve().parents[1]
    cli = release/'QingdanCLI.exe'
    desktop = release/'Qingdan.exe'
    for source in (project/'src/qingdan/web').iterdir():
        if source.is_file():
            bundled = release/'qingdan/web'/source.name
            assert hashlib.sha256(source.read_bytes()).digest() == hashlib.sha256(bundled.read_bytes()).digest(), source.name
    with tempfile.TemporaryDirectory(prefix='qingdan-release-',dir=project/'buildtmp') as tmp:
        data = Path(tmp)/'data'
        def command(*args):
            result = subprocess.run([str(cli),'--data-dir',str(data),*args],capture_output=True,
                                    encoding='utf-8',timeout=30)
            assert result.returncode == 0,(args,result.returncode,result.stderr)
            return json.loads(result.stdout)
        group = command('group','create','--name','验收清单')['id']
        title = '中文 "引号" 与 \\路径'
        task = command('task','create','--title',title,'--list-id',group,
                       '--due-at','2027-01-31T09:00:00','--repeat','monthly',
                       '--tags',json.dumps(['验收标签'],ensure_ascii=False))
        assert task['data']['title']==title
        rolled = command('task','complete','--item-id',task['id'])
        assert rolled['data']['due_at']=='2027-02-28T09:00:00'
        labels = command('tag','list')
        assert labels['items'][0]['data']['name']=='验收标签'
        command('tag','rename','--item-id',labels['items'][0]['id'],'--name','已改标签')
        updated = command('task','update','--item-id',task['id'],
                          '--changes',json.dumps({'note':'修改内容'},ensure_ascii=False))
        assert updated['data']['tags']==['已改标签']
        assert updated['data']['note']=='修改内容'
        moved = command('task','move','--item-id',task['id'],'--to-inbox')
        assert moved['data']['list_id'] is None
        preview = command('task','repeat_preview','--repeat','FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,FR',
                          '--due-at','2026-11-13T09:00:00')
        assert preview['upcoming']
        failed = subprocess.run([str(cli),'--data-dir',str(data),'task','create','--title',''],
                                capture_output=True,encoding='utf-8',timeout=30)
        assert failed.returncode==2
        assert json.loads(failed.stderr)['error']['code']=='VALIDATION_ERROR'
        snapshot = command('backup','create','--force')
        process = subprocess.Popen([str(desktop),'serve',str(data)])
        try:
            deadline = time.monotonic()+20
            while not (data/'http.json').is_file():
                assert process.poll() is None,'frozen service stopped before startup'
                if time.monotonic()>deadline: raise TimeoutError('frozen service startup')
                time.sleep(.1)
            port=json.loads((data/'http.json').read_text(encoding='utf-8'))['port']
            url=f'http://127.0.0.1:{port}'
            while True:
                assert process.poll() is None, "frozen service exited during startup"
                try:
                    with urllib.request.urlopen(url+'/api/v1/health',timeout=2) as response:
                        health=json.load(response)
                    break
                except OSError:
                    if time.monotonic()>deadline: raise
                    time.sleep(.1)
            assert health['ok'] and health['data']['tasks']==1
            command('backup','restore','--name',snapshot['name'])
            live = command('task','create','--title','Live CLI request')
            assert live['data']['title']=='Live CLI request'
            assert (data/'restore_pending.json').is_file(), 'CLI must not hot-restore a running database'
            with urllib.request.urlopen(url+'/api/v1/health',timeout=2) as response:
                assert json.load(response)['data']['tasks']==2

            with urllib.request.urlopen(url+'/api/v1/meta/features',timeout=2) as response:
                features=json.load(response)['data']
            assert {'tag.get','group.get','task.repeat_preview','task.move'} <= features.keys()
            def request(base,path,method='GET',body=None,key=None,context=None):
                headers={'Content-Type':'application/json'}
                if key: headers['Authorization']='Bearer '+key
                req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,
                                           headers=headers,method=method)
                try:
                    with urllib.request.urlopen(req,timeout=10,context=context) as response:
                        return response.status,json.load(response)
                except urllib.error.HTTPError as error:
                    return error.code,json.load(error)
            _,issued=request(url,'/api/v1/access/tokens','POST',
                             {'name':'Release read-only','permissions':['read'],'features':['task.list']})
            secret=issued['data']['token']; token_id=issued['data']['item']['id']
            with socket.socket() as probe:
                probe.bind(('127.0.0.1',0)); remote_port=probe.getsockname()[1]
            remote_url=f'http://127.0.0.1:{remote_port}'
            code,_=request(url,'/api/v1/access/config','PATCH',{'config':{
                'enabled':True,'host':'127.0.0.1','port':remote_port,'transport':'http'}})
            assert code==200
            assert request(remote_url,'/api/v1/tasks')[0]==401
            assert request(remote_url,'/api/v1/tasks',key=secret)[0]==200
            assert request(remote_url,'/api/v1/tasks','POST',{'title':'Denied'},secret)[0]==403
            assert request(remote_url,'/api/v1/access/tokens',key=secret)[0]==403
            assert request(url,f'/api/v1/access/tokens/{token_id}','PATCH',
                           {'permissions':['create'],'features':['task.create']})[0]==200
            assert request(remote_url,'/api/v1/tasks',key=secret)[0]==403
            assert request(remote_url,'/api/v1/tasks','POST',{'title':'Remote release smoke'},secret)[0]==200
            assert request(url,f'/api/v1/access/tokens/{token_id}','DELETE')[0]==200
            assert request(remote_url,'/api/v1/tasks',key=secret)[0]==401
            tls_verified=False
            openssl=Path('C:/Program Files/Git/usr/bin/openssl.exe')
            if openssl.is_file():
                cert,keyfile=Path(tmp)/'cert.pem',Path(tmp)/'key.pem'
                subprocess.run([str(openssl),'req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                                '-keyout',str(keyfile),'-out',str(cert),'-subj','/CN=localhost',
                                '-addext','subjectAltName=IP:127.0.0.1,DNS:localhost'],
                               capture_output=True,check=True)
                assert request(url,'/api/v1/access/config','PATCH',{'config':{
                    'transport':'https','tls_cert':str(cert),'tls_key':str(keyfile)}})[0]==200
                _,issued=request(url,'/api/v1/access/tokens','POST',{'name':'TLS reader'})
                trust=ssl.create_default_context(cafile=str(cert))
                assert request(f'https://127.0.0.1:{remote_port}','/api/v1/tasks',
                               key=issued['data']['token'],context=trust)[0]==200
                tls_verified=True
            assert request(url,'/api/v1/access/config','PATCH',{'config':{'enabled':False}})[0]==200
            print(json.dumps({'ok':True,'features':len(features),'cli':True,'monthly_rollover':True,'frozen_http':True,'live_cli_forwarding':True,
                              'bundled_web_matches_source':True,'remote_token_permissions':True,'frozen_https':tls_verified},ensure_ascii=False))
        finally:
            process.terminate()
            process.wait(timeout=10)


if __name__=='__main__':
    verify(Path(sys.argv[1] if len(sys.argv)>1 else 'dist/release-20261111-final/Qingdan'))
