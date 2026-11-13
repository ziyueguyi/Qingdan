"""Bounded board columns and card pages, using the same SQL task predicates."""
from qingdan.core import task_query, view
from qingdan.core.registry import not_found, validation_error


def query(store, today, group_by='status', list_id=None, tag=None, name='all',
          include_done=False, filter_id=None, sort='smart', reverse=False,
          limit=0, offset=0, column=None, columns_limit=0, columns_offset=0):
    limit, offset = task_query.paging(limit, offset)
    if group_by not in ('status', 'priority', 'group'):
        raise validation_error('board 的 group_by 只能是 status/group/priority')
    if not 0 <= columns_limit <= 20 or columns_offset < 0:
        raise validation_error('columns_limit 需在 0..20，columns_offset 不能为负数')
    task_query.order_by(sort, reverse)
    with store.read_snapshot():
        where, args = task_query.view_where(name, today, include_done, list_id, tag)
        if filter_id:
            item = store.get(filter_id)
            if item is None or item.deleted or item.kind != 'filter':
                raise not_found('过滤器不存在')
            criteria, values = task_query.criteria_where(item.data.get('criteria') or {}, today)
            where.extend(criteria); args.extend(values)
        db = task_query.connection(store)
        predicate = ' AND '.join(f'({clause})' for clause in where)
        total = db.execute(f'SELECT COUNT(*) FROM item i WHERE {predicate}', args).fetchone()[0] if column is None else None
        if group_by == 'group':
            expression = "coalesce(nullif(json_extract(i.data,'$.list_id'),''),'inbox')"
            if column is not None:
                columns_total, keys = 1, [(column, None)]
            else:
                columns_total = db.execute(f'SELECT COUNT(DISTINCT {expression}) FROM item i WHERE {predicate}', args).fetchone()[0]
                keys = [(str(row[0]), row[1]) for row in db.execute(
                    f'SELECT {expression},COUNT(*),MIN(i.id) first_id FROM item i WHERE {predicate} '
                    f'GROUP BY {expression} ORDER BY first_id LIMIT ? OFFSET ?', [*args, columns_limit or -1, columns_offset])]
        else:
            expression = task_query.STATUS if group_by == 'status' else f'CAST({task_query.PRIORITY} AS TEXT)'
            all_keys = list(view._STATUS_KEYS) if group_by == 'status' else ['1','2','3','4']
            if column is not None and column not in all_keys:
                raise validation_error('未知看板列', column=column)
            if column is not None:
                columns_total, keys = 1, [(column,None)]
            else:
                counts = dict(db.execute(f'SELECT {expression},COUNT(*) FROM item i WHERE {predicate} GROUP BY {expression}', args))
                columns_total = len(all_keys)
                keys = [(key, counts.get(key, 0)) for key in
                        all_keys[columns_offset:columns_offset+columns_limit if columns_limit else None]]
        columns, items = [], []
        names = store.list_names(key for key, _ in keys) if group_by == 'group' else {}
        for key, count in keys:
            members, page = task_query.select(store, [*where, f'{expression}=?'], [*args,key],
                                               sort, reverse, limit, offset if column is not None else 0,
                                               known_total=count)
            names.update(store.list_names(item.data.get('list_id') for item in members))
            payloads = [item.to_dict() for item in members]
            items.extend(payloads)
            label_key = view._STATUS_KEYS[key] if group_by == 'status' else (
                view._PRIORITY_KEYS[int(key)] if group_by == 'priority' else 'inbox' if key == 'inbox' else None)
            columns.append({'key':key,'label_key':label_key,'label':names.get(key,'') if label_key is None else None,
                            **page,'items':payloads})
        selected = columns[0] if column is not None and columns else None
        return {'group_by':group_by, 'total':selected['total'] if selected else total, 'board_total':total,
                'columns':columns,'columns_total':columns_total,'columns_limit':columns_limit if column is None else 1,
                'columns_offset':columns_offset if column is None else 0,'columns_has_more':column is None and columns_offset+len(columns)<columns_total,
                'items':items,'list_names':names,'offset':selected['offset'] if selected else 0,
                'limit':limit,'has_more':selected['has_more'] if selected else False,
                'change_token':store.task_change_token()}
