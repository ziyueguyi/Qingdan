import assert from 'node:assert/strict';
const {TaskPager} = await import(process.argv[2]);
const tick = () => new Promise(resolve=>setTimeout(resolve,0));
const page = (offset, revision=[1], total=1000) => ({offset,limit:50,total,has_more:offset+50<total,change_token:revision,items:[{id:String(offset)}]});
const passed = [];

{
  const calls = [];
  const pager = new TaskPager(async (feature,params)=>{calls.push([feature,params]);return page(params.offset);});
  pager.configure('view.query',{name:'all'},[1]);
  const first = await pager.get(0);
  pager.warm(0, first); await tick();
  assert.deepEqual(calls.map(x=>x[1].offset),[0,50]);
  assert(calls.every(x=>x[1].limit===50));
  await pager.get(50); assert.equal(calls.length,2);
  for(let offset=100;offset<950;offset+=50) {
    const result=await pager.get(offset);pager.warm(offset,result);await tick();
    assert(pager.cache.size<=3);
  }
  assert.deepEqual([...pager.cache.keys()].sort((a,b)=>a-b),[850,900,950]);
  passed.push('adjacent prefetch, cache reuse, bounded memory');
}

{
  let release, calls=0;
  const pager=new TaskPager(()=>{calls++;return new Promise(resolve=>release=resolve);});
  pager.configure('search.query',{q:'same'},[1]);
  const a=pager.get(0),b=pager.get(0);await tick();
  assert.equal(calls,1);release(page(0));
  const [one,two]=await Promise.all([a,b]);assert.equal(one,two);
  passed.push('duplicate requests coalesce');
}

{
  const pager=new TaskPager(async()=>page(0,[2]));
  pager.configure('view.query',{},[1]);
  await assert.rejects(pager.get(0),e=>e.code==='PAGE_CHANGED');
  assert.equal(pager.cache.size,0);assert.equal(pager.pending.size,0);
  passed.push('revision mismatch cannot enter cache');
}

{
  const requests=[];let releaseOld,releaseNew;
  const pager=new TaskPager((feature,params)=>new Promise(resolve=>{
    requests.push([feature,params]);
    if(feature==='old')releaseOld=resolve;else releaseNew=resolve;
  }));
  pager.configure('old',{q:'old'},[1]);const old=pager.get(0);
  // Reconfigure before the deferred fetch even starts.
  pager.configure('new',{q:'new'},[2]);const next=pager.get(0);await tick();
  assert.deepEqual(requests.map(x=>[x[0],x[1].q]),[['old','old'],['new','new']]);
  releaseOld(page(0,[1]));await old;
  assert.equal(pager.cache.size,0);assert(pager.pending.has(0));
  releaseNew(page(0,[2]));await next;
  assert.deepEqual(pager.cache.get(0).change_token,[2]);
  passed.push('stale descriptor, response and finally cannot corrupt new query');
}

{
  let failures=0;
  const pager=new TaskPager(async (_,params)=>{
    if(params.offset===50 && failures++===0)throw new Error('offline');
    return page(params.offset);
  });
  pager.configure('view.query',{},[1]);
  const first=await pager.get(0);pager.warm(0,first);await tick();
  assert(!pager.cache.has(50));assert.equal(pager.pending.size,0);
  const retry=await pager.get(50);assert.equal(retry.offset,50);
  assert.equal(failures,2);
  passed.push('prefetch failure can be retried on demand');
}

console.log(JSON.stringify({passed}));
