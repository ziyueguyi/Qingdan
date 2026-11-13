"""SQL task selection. Decode only returned rows; retain pure view.py as oracle.

Connection-local date/lower functions preserve Python timezone/Unicode semantics.
No persistent index depends on them, so older clients can still open the DB.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

from qingdan.core import view
from qingdan.core.registry import validation_error


def field(name: str) -> str:
    # Names are source constants, never user input.
    return f"json_extract(i.data,'$.{name}')"


STATUS = field('status')
OPEN = f"coalesce({STATUS},'') NOT IN ('done','cancelled')"
DUE = f"qd_day({field('due_at')})"
PRIORITY = f"coalesce(CAST({field('priority')} AS INTEGER),4)"


def connection(store):
    db = store.db
    # Repeated dates are common in counts/stats. Bound the per-operation cache,
    # and recreate it each time so changing the machine timezone cannot leave
    # an old wall-clock conversion cached across requests.
    @lru_cache(maxsize=512)
    def day(value):
        return d.isoformat() if (d := view.to_date(value)) else None
    db.create_function('qd_day', 1, day)
    db.create_function('qd_lower', 1, lambda value: str(value or '').lower())
    return db


def paging(limit: int, offset: int) -> tuple[int, int]:
    limit, offset = int(limit), int(offset)
    if limit < 0 or limit > 500 or offset < 0:
        raise validation_error('limit 需在 0..500，offset 不能为负数')
    return limit, offset


def view_where(name, today, include_done=False, list_id=None, tag=None):
    where, args = ["i.kind='task'"], []
    if name == 'trash':
        where.append('i.deleted=1')
    else:
        where.append('i.deleted=0')
        if name == 'done':
            where.append(f"{STATUS}='done'")
        elif name in ('today', 'next7'):
            where.append(f"({OPEN}" + (f" OR {STATUS}='done')" if include_done else ')'))
            where.append(f'{DUE}<=?')
            args.append((today + timedelta(days=6 if name == 'next7' else 0)).isoformat())
        elif name == 'inbox':
            where.append(f"coalesce({field('list_id')},'')=''")
            if not include_done:
                where.append(OPEN)
        elif name != 'all':
            raise validation_error('未知视图', got=name)
    if list_id:
        where.append(f"{field('list_id')}=?")
        args.append(list_id)
    if tag:
        where.append("EXISTS(SELECT 1 FROM json_each(i.data,'$.tags') t WHERE t.value=?)")
        args.append(tag)
    return where, args


def criteria_where(criteria, today):
    where, args = ["i.kind='task'", 'i.deleted=0'], []
    for key, target in [('status', 'status'), ('list_id', 'list_id'), ('group_id', 'list_id')]:
        if criteria.get(key):
            where.append(f'{field(target)}=?')
            args.append(criteria[key])
    if criteria.get('open_only'):
        where.append(OPEN)
    if criteria.get('no_group'):
        where.append(f"coalesce({field('list_id')},'')=''")
    if criteria.get('tag'):
        where.append("EXISTS(SELECT 1 FROM json_each(i.data,'$.tags') t WHERE t.value=?)")
        args.append(criteria['tag'])
    if criteria.get('tags_any'):
        tags = list(criteria['tags_any'])
        where.append("EXISTS(SELECT 1 FROM json_each(i.data,'$.tags') t WHERE t.value IN (" + ','.join('?' for _ in tags) + '))')
        args.extend(tags)
    for key, op in [('priority', '='), ('priority_max', '<=')]:
        if criteria.get(key):
            where.append(f'{PRIORITY}{op}?')
            args.append(int(criteria[key]))
    if criteria.get('due_within_days') is not None:
        where.append(f'{DUE} BETWEEN ? AND ?')
        def bound(days):
            return date.fromordinal(max(1, min(date.max.toordinal(), today.toordinal() + int(days)))).isoformat()
        args.extend([bound(criteria.get('due_from_days', -10**6)), bound(criteria['due_within_days'])])
    if criteria.get('overdue'):
        where.append(f'{DUE}<?')
        args.append(today.isoformat())
    if criteria.get('has_due'):
        where.append(f"coalesce({field('due_at')},'')<>''")
    if criteria.get('q'):
        def text(name):
            return f"CASE WHEN json_type(i.data,'$.{name}')='null' THEN 'None' ELSE coalesce({field(name)},'') END"
        hay = f"({text('title')})||' '||({text('note')})||' '||coalesce((SELECT group_concat(t.value,' ') FROM json_each(i.data,'$.tags') t),'')"
        where.append(f'instr(qd_lower({hay}),?)>0')
        args.append(str(criteria['q']).lower())
    return where, args


def order_by(mode, reverse=False):
    due = f"coalesce(nullif({field('due_at')},''),'{view.SMART_DUE_MAX}')"
    created = f"coalesce({field('created_at')},'')"
    order = f"coalesce(CAST({field('order')} AS INTEGER),0)"
    keys = {
        'smart': [due, PRIORITY, f"{field('due_at')} IS NULL", order, created, 'i.id'],
        'due': [due, 'i.id'], 'priority': [PRIORITY, 'i.id'],
        'title': [f"coalesce({field('title')},'')", 'i.id'],
        'created': [created, 'i.id'], 'updated': ['i.rev_ms', 'i.rev_seq', 'i.rev_dev', 'i.id'],
        'manual': [order, 'i.id'],
    }
    if mode not in keys:
        raise validation_error('未知排序方式', got=mode)
    return ','.join(f'({key}) ' + ('DESC' if reverse else 'ASC') for key in keys[mode])


def select(store, where, args, sort='smart', reverse=False, limit=0, offset=0, order=None):
    limit, offset = paging(limit, offset)
    db = connection(store)
    predicate = ' AND '.join(f'({clause})' for clause in where)
    with store.read_snapshot():
        total = db.execute(f'SELECT COUNT(*) FROM item i WHERE {predicate}', args).fetchone()[0]
        rows = db.execute(f'SELECT i.* FROM item i WHERE {predicate} ORDER BY {order or order_by(sort, reverse)} LIMIT ? OFFSET ?',
                          [*args, limit or -1, offset])
        items = [store._row_to_item(row) for row in rows]
        token = store.task_change_token()
        return items, {'total': total, 'limit': limit, 'offset': offset,
                       'has_more': offset + len(items) < total, 'change_token': token}
