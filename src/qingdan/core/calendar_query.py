"""Calendar counts and bounded event pages in one consistent read snapshot."""
from datetime import date, timedelta
from functools import lru_cache

from qingdan.core import recurrence_window, rrule, task_query, view
from qingdan.core.registry import not_found, validation_error


def query(store, start, days, today, include_countdown=True, list_id=None,
          limit=0, offset=0, tag=None, filter_id=None, name='all'):
    limit, offset = task_query.paging(limit, offset)
    if not 1 <= days <= 370 or (offset and days != 1):
        raise validation_error('days 需在 1..370；offset 仅适用于单日查询')
    if start.toordinal()+days-1 > date.max.toordinal():
        raise validation_error('日历窗口超出合法日期范围')
    end = start+timedelta(days=days-1)
    with store.read_snapshot():
        where, args = task_query.view_where(name,today,True,list_id,tag)
        if filter_id:
            item = store.get(filter_id)
            if item is None or item.deleted or item.kind != 'filter':
                raise not_found('过滤器不存在')
            clauses, values = task_query.criteria_where(item.data.get('criteria') or {},today)
            where.extend(clauses); args.extend(values)
        predicate = ' AND '.join(f'({clause})' for clause in where)
        if include_countdown and not (list_id or tag or filter_id) and name == 'all':
            predicate = f"(({predicate}) OR (i.kind='countdown' AND i.deleted=0))"
        db = task_query.connection(store)
        day = "qd_day(CASE WHEN i.kind='countdown' THEN json_extract(i.data,'$.target_date') ELSE json_extract(i.data,'$.due_at') END)"
        real_where = f'{predicate} AND {day} BETWEEN ? AND ?'
        real_args = [*args,start.isoformat(),end.isoformat()]
        real_counts = dict(db.execute(f'SELECT {day},COUNT(*) FROM item i WHERE {real_where} GROUP BY {day}',real_args))
        # Window functions select only current event IDs, never all Item JSON.
        selected = { (start+timedelta(days=i)).isoformat():[] for i in range(days) }
        ordering = task_query.order_by('smart')
        rows = db.execute(f'SELECT id,event_day FROM (SELECT i.id,{day} event_day,ROW_NUMBER() OVER (PARTITION BY {day} ORDER BY {ordering}) position FROM item i WHERE {real_where}) WHERE position>? AND (?=0 OR position<=?) ORDER BY event_day,position',
                          [*real_args,offset,limit,offset+limit])
        for row in rows:
            selected[row['event_day']].append((row['id'],False))
        warnings = []
        @lru_cache(maxsize=128)
        def parse(raw):
            return rrule.try_parse(raw)
        @lru_cache(maxsize=512)
        def dates(raw,due,anchor):
            if not due:
                return ()
            rule = parse(raw)
            return tuple(d.isoformat() for d in recurrence_window.dates(
                view.as_date(due),rule,view.to_date(anchor),max(start,today),end,warnings))
        signature = f"json_extract(i.data,'$.repeat'),{task_query.DUE},qd_day(json_extract(i.data,'$.repeat_start'))"
        recurring_where = f"{predicate} AND i.kind='task' AND i.deleted=0 AND {task_query.OPEN} AND coalesce(json_extract(i.data,'$.repeat'),'')<>'' AND {task_query.DUE}<?"
        recurring_args = [*args,end.isoformat()]
        counts = {}
        for raw,due,anchor,count in db.execute(f'SELECT {signature},COUNT(*) FROM item i WHERE {recurring_where} GROUP BY {signature}',recurring_args):
            for value in dates(raw,due,anchor):
                counts[value] = counts.get(value,0)+count
        needed, skip = {}, {}
        for value in selected:
            real = real_counts.get(value,0)
            wanted = counts.get(value,0) if not limit else min(counts.get(value,0),max(0,limit-len(selected[value])))
            if wanted:
                needed[value] = wanted
                skip[value] = max(0,offset-real)
        if needed:
            for row in db.execute(f'SELECT i.id,{signature} FROM item i WHERE {recurring_where} ORDER BY i.id',recurring_args):
                for value in dates(row[1],row[2],row[3]):
                    if value not in needed:
                        continue
                    if skip[value]:
                        skip[value] -= 1
                    else:
                        selected[value].append((row[0],True))
                        needed[value] -= 1
                        if not needed[value]:
                            del needed[value]
                if not needed:
                    break
        ids = list(dict.fromkeys(key for events in selected.values() for key,_ in events))
        items = {}
        for index in range(0,len(ids),100):
            part = ids[index:index+100]
            for row in db.execute('SELECT * FROM item WHERE id IN ('+','.join('?' for _ in part)+')',part):
                item = store._row_to_item(row)
                items[item.id] = item
        cells = []
        for value,events in selected.items():
            cur = view.as_date(value)
            total = real_counts.get(value,0)+counts.get(value,0)
            cells.append({'date':value,'in_month':cur.month==start.month,'is_today':cur==today,
                          'count':total,'recurring':counts.get(value,0),'limit':limit,'offset':offset,
                          'has_more':offset+len(events)<total,
                          'events':[view.event_shape(items[key],cur,today,virtual) for key,virtual in events]})
        return {'start':start.isoformat(),'days':days,'today':today.isoformat(),'cells':cells,
                'warnings':warnings,'change_token':store.task_change_token(),
                'items':cells[0]['events'] if days==1 else [],'total':cells[0]['count'] if days==1 else sum(c['count'] for c in cells),
                'limit':limit,'offset':offset,'has_more':days==1 and cells[0]['has_more']}
