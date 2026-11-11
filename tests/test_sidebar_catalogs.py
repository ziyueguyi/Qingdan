import pytest
from qingdan.core.registry import call, QdError


@pytest.mark.parametrize('kind,update', [('group','update'),('tag','rename'),('filter','update')])
def test_catalog_icon_persists_and_can_reset(kind,update,app_ctx):
    args={'criteria':{'overdue':True}} if kind=='filter' else {}
    item=call(kind+'.create',name='Custom',icon='⭐',**args)
    assert item['data']['icon']=='⭐'
    assert call(kind+'.get',item_id=item['id'])['data']['icon']=='⭐'
    call(kind+'.'+update,item_id=item['id'],icon='')
    assert call(kind+'.get',item_id=item['id'])['data']['icon']==''
    with pytest.raises(QdError):call(kind+'.'+update,item_id=item['id'],icon='x'*17)
    assert call(kind+'.get',item_id=item['id'])['data']['icon']==''


def test_filter_pages_use_sql_and_do_not_materialize_entire_catalog(app_ctx,monkeypatch):
    ids=[]
    for i in range(17):ids.append(call('filter.create',name=f'Filter {i:02}',criteria={'overdue':True})['id'])
    def forbidden(*args,**kwargs):raise AssertionError('must paginate in SQL')
    monkeypatch.setattr(app_ctx.store,'list_items',forbidden)
    page=call('filter.list',limit=5,offset=5)
    assert page['total']==17 and [x['id'] for x in page['items']]==ids[5:10]
    assert page['next_offset']==10
    assert call('filter.list',q='16')['items'][0]['id']==ids[16]


def test_implicit_tag_can_receive_a_custom_icon_without_duplicate(app_ctx):
    call('task.create',title='Tagged',tags=['Implicit'])
    tag=call('tag.list')['items'][0]
    call('tag.rename',item_id=tag['id'],icon='💼')
    result=call('tag.list')
    assert result['total']==1 and result['items'][0]['data']['icon']=='💼'
    assert call('task.list')['items'][0]['data']['tags']==['Implicit']
