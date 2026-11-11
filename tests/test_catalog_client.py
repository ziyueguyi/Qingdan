"""Browser-independent behavioral coverage for bounded, prefetched catalog pages."""
import shutil
import subprocess
from pathlib import Path
import pytest


def test_catalog_prefetch_and_bounded_cache(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node unavailable')
    web = Path(__file__).resolve().parents[1]/'src/qingdan/web'
    for name in ('catalog.js','api.js'):
        shutil.copy2(web/name,tmp_path/name)
    (tmp_path/'package.json').write_text('{"type":"module"}',encoding='utf-8')
    script = tmp_path/'verify.mjs'
    script.write_text('''
import assert from 'node:assert/strict';
import {loadFeatures} from './api.js';
import {catalogs,refreshCatalogs,scrollCatalog} from './catalog.js';
const requests=[];
globalThis.fetch=async (url) => {
  if(url==='/api/v1/meta/features')return {ok:true,json:async()=>({ok:true,data:{
    'tag.list':{http:['GET','/api/v1/tags']},'group.list':{http:['GET','/api/v1/groups']}
    ,'filter.list':{http:['GET','/api/v1/filters']}
  }})};
  const parsed=new URL(url,'http://local');
  const offset=Number(parsed.searchParams.get('offset')),limit=Number(parsed.searchParams.get('limit'));
  requests.push({path:parsed.pathname,offset,limit});
  return {ok:true,json:async()=>({ok:true,data:{total:27,offset,items:Array.from({length:Math.min(5,27-offset)},(_,i)=>({id:String(offset+i),data:{name:'item'+(offset+i)}}))}})};
};
await loadFeatures();
await refreshCatalogs();
assert.deepEqual([...catalogs.tag.pages.keys()],[0,1]);
assert.deepEqual([...catalogs.group.pages.keys()],[0,1]);
assert.deepEqual([...catalogs.filter.pages.keys()],[0,1]);
assert.equal(requests.length,6);
await scrollCatalog('tag',180);
assert.equal(catalogs.tag.page,1);
assert.deepEqual([...catalogs.tag.pages.keys()],[0,1,2]);
assert.equal(requests.length,7); // current page already prefetched; only fetch the following page
await scrollCatalog('tag',900);
assert.equal(catalogs.tag.page,5);
assert.ok(catalogs.tag.pages.size<=3);
assert.ok([...catalogs.tag.pages.keys()].every(p=>Math.abs(p-5)<=1));
assert.ok(requests.every(r=>r.limit===5));
await scrollCatalog('filter',180);
assert.equal(catalogs.filter.page,1);
assert.ok(catalogs.filter.pages.size<=3);
''',encoding='utf-8')
    result = subprocess.run([node,str(script)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr

