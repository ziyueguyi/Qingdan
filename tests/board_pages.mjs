import assert from 'node:assert/strict';
const {BoardPager} = await import(process.argv[2]);
const tick = () => new Promise(resolve=>setTimeout(resolve,0));
const make = (p,revision=1) => {
  const card = key => ({key,label:key,total:200,offset:p.column ? p.offset : 0,limit:p.limit,
    has_more:(p.column ? p.offset : 0)+p.limit<200,change_token:revision,
    items:Array.from({length:p.limit},(_,i)=>({id:key+'-'+((p.column ? p.offset : 0)+i)}))});
  const columns=p.column ? [card(p.column)] : Array.from({length:Math.min(p.columns_limit,40-p.columns_offset)},(_,i)=>card('G'+(p.columns_offset+i)));
  return {columns,items:columns.flatMap(c=>c.items),list_names:{},total:p.column ? 200 : 8000,
    columns_total:40,columns_offset:p.columns_offset || 0,columns_limit:p.columns_limit || 0,
    columns_has_more:(p.columns_offset || 0)+columns.length<40,change_token:revision};
};

{
  const calls=[];
  const pager=new BoardPager(async (feature,p)=>{calls.push(p);return make(p);});
  pager.configure({group_by:'group'},1);
  const first=await pager.get(0);await tick();
  assert.equal(first.columns.length,5);assert(first.columns.every(c=>c.items.length===20));
  assert(calls.some(p=>p.columns_offset===5));
  assert(calls.some(p=>p.column==='G0' && p.offset===20));
  const count=calls.length;
  const cards=await pager.cards('G0',20);
  assert.equal(cards.items[0].id,'G0-20');
  assert(!calls.slice(count).some(p=>p.column==='G0' && p.offset===20));
  assert.equal((await pager.get(0)).columns[0].offset,20);
  const window=first.window;
  for(let offset=5;offset<40;offset+=5){
    const result=await pager.get(offset);await tick();
    assert.equal(result.columns[0].key,'G'+offset);
    assert(pager.pagers.size<=5);assert(pager.groups.cache.size<=3);
    assert([...pager.pagers.values()].every(p=>p.cache.size<=3));
  }
  assert(pager.window>window);
}

{
  const releases=new Map();
  const pager=new BoardPager(async (feature,p)=>{
    if(!p.column && [0,5].includes(p.columns_offset))return new Promise(resolve=>releases.set(p.columns_offset,()=>resolve(make(p))));
    return make(p);
  });
  pager.configure({group_by:'group'},1);
  const old=pager.get(0);await tick();const newer=pager.get(5);await tick();
  releases.get(5)();const result=await newer;
  releases.get(0)();assert.equal(await old,null);
  assert.equal(result.columns[0].key,'G5');assert.equal(pager.visibleOffset,5);
  assert(!pager.pagers.has('G0'));
}

{
  let release;
  const pager=new BoardPager(async (feature,p)=>{
    if(p.column==='G0' && p.offset===20)return new Promise(resolve=>release=()=>resolve(make(p)));
    return make(p);
  });
  pager.configure({group_by:'group'},1);
  await pager.get(0);await tick();
  const cards=pager.cards('G0',20),refresh=pager.get(0);await tick();release();
  await cards;assert.equal((await refresh).columns[0].offset,20);
}
{
  let release;
  const pager=new BoardPager(async (feature,p)=>{
    if(p.tag==='old' && !p.column && p.columns_offset===0)return new Promise(resolve=>release=()=>resolve(make(p)));
    return make(p);
  });
  pager.configure({group_by:'group',tag:'old'},1);
  const old=pager.get(0);await tick();
  pager.configure({group_by:'group',tag:'new'},1);
  await pager.get(0);
  const current=pager.pagers.get('G0');
  release();assert.equal(await old,null);
  assert.equal(pager.pagers.get('G0'),current);
  assert.equal(JSON.parse(current.key)[1].tag,'new');
}
console.log('board pagination: bounded columns/cards, prefetch, stale response and refresh races passed');
