"""Local, paged notification targets shared by platform notification adapters."""
import json

from qingdan.core import stamp, task_query


def revision(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'))


def token(store):
    return int(store.db.execute('SELECT value FROM notification_revision WHERE id=1').fetchone()[0])


def total(store):
    return int(store.db.execute('SELECT COUNT(*) FROM notification_item').fetchone()[0])


def capture(messages,store=None):
    if store is None:
        from qingdan.core.context import ctx
        store=ctx.need_store()
    now=stamp.now_ms()
    rows=[(m.item_id,m.kind,revision(m.revision),m.fire_at_ms,now) for m in messages if m.revision]
    store.db.executemany('''INSERT INTO notification_item(item_id,kind,revision,fire_at,received_at)
        VALUES(?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET kind=excluded.kind,
        revision=excluded.revision,fire_at=excluded.fire_at,received_at=excluded.received_at
        WHERE notification_item.revision<>excluded.revision OR notification_item.fire_at<>excluded.fire_at''',rows)
    store.db.commit()
    return total(store)


def acknowledge(store,item_id,expected):
    store.db.execute('DELETE FROM notification_item WHERE item_id=? AND revision=?',(item_id,revision(expected)))
    store.db.commit()
    return total(store)


def query(store,limit=50,offset=0):
    limit,offset=task_query.paging(limit,offset)
    # The notification inbox has no unbounded mode, including API/CLI callers.
    limit=limit or 50
    with store.read_snapshot():
        count=total(store)
        rows=store.db.execute('''SELECT i.*,n.item_id notification_id,n.kind notification_kind,
            n.revision notification_snapshot,n.fire_at notification_fire,n.received_at notification_received
            FROM notification_item n LEFT JOIN item i ON i.id=n.item_id
            ORDER BY n.received_at DESC,n.item_id LIMIT ? OFFSET ?''',(limit,offset))
        items=[]
        for row in rows:
            item=store._row_to_item(row) if row['id'] is not None else None
            expected=json.loads(row['notification_snapshot'])
            status=('gone' if item is None or item.deleted else
                    'inactive' if item.data.get('status') in ('done','cancelled') else
                    'stale' if item.rev.to_json()!=expected else 'open')
            items.append({'id':row['notification_id'],'kind':row['notification_kind'],
                          'title':(item.data.get('title') or item.data.get('name') or '') if item else '',
                          'expected_rev':expected,'status':status,'fire_at':row['notification_fire'],
                          'received_at':row['notification_received']})
        return {'items':items,'total':count,'limit':limit,'offset':offset,'has_more':offset+len(items)<count,
                'change_token':[store.task_change_token(),token(store)]}
