"""Seek a recurrence window without consuming a fixed preview before it.

Ranks retain COUNT's meaning from the original anchor. Gregorian monthly
patterns repeat every 400 years; counting the prefix never walks arbitrary
amounts of history. This module creates no task instances or database rows.
"""
from dataclasses import replace
from datetime import date, timedelta
from functools import lru_cache
from math import gcd

from qingdan.core import rrule


@lru_cache(maxsize=8)
def _month_prefix(anchor, step, month_days, cycles):
    rule=rrule.Rule(freq='MONTHLY',bymonthday=month_days)
    prefix = [0]
    for k in range(cycles):
        # Equivalent month/year in the Gregorian 400-year cycle.
        index = (anchor.year % 400) * 12 + anchor.month - 1 + step*k
        year, month = index//12 % 400 + 2000, index % 12 + 1
        prefix.append(prefix[-1] + len(rrule._month_days(year, month, rule, anchor.day)))
    return tuple(prefix)


def dates(due, rule, anchor, start, end, warnings=None, limit=None):
    """Future occurrences, inclusive window, strictly after current due date."""
    if rule is None or start > end or due == date.max:
        return []
    a = min(anchor or due, due)
    lower = max(start, due + timedelta(days=1))
    end = min(end, rule.until or date.max)
    if lower > end:
        return []
    if rule.workday:
        # A missing calendar year remains an explicit failure/warning. The
        # bundled two-year calendar bounds this walk to at most 731 days.
        result = []
        try:
            for value in rrule.occurrences(a, replace(rule, until=end)):
                if value >= lower:
                    result.append(value)
                    if limit is not None and len(result)>=limit:
                        break
        except rrule.CalendarError as error:
            if warnings is None:
                raise
            warning = {'key':'calendar_missing_year','calendar_id':error.calendar_id,'year':error.year}
            if warning not in warnings:
                warnings.append(warning)
        return result
    result = []
    def append(value, rank):
        if value > end or (rule.count is not None and rank > rule.count):
            return False
        if value >= lower:
            result.append(value)
            if limit is not None and len(result)>=limit:
                return False
        return True
    if rule.freq == 'DAILY' or (rule.freq == 'WEEKLY' and not rule.byday):
        step = rule.interval * (7 if rule.freq == 'WEEKLY' else 1)
        k = max(0, (lower.toordinal()-a.toordinal()+step-1)//step)
        ordinal = a.toordinal() + k*step
        while ordinal <= end.toordinal():
            if not append(date.fromordinal(ordinal), k+1):
                break
            k += 1; ordinal += step
    elif rule.freq == 'WEEKLY':
        monday = a.toordinal()-a.weekday()
        step = 7*rule.interval
        k = max(0, (lower.toordinal()-monday)//step)
        first = [wd for wd in rule.byday if wd >= a.weekday()]
        while monday+k*step <= end.toordinal():
            days = first if k == 0 else rule.byday
            rank = 0 if k == 0 else len(first)+(k-1)*len(rule.byday)
            for index, wd in enumerate(days):
                ordinal = monday+k*step+wd
                if ordinal > date.max.toordinal() or not append(date.fromordinal(ordinal), rank+index+1):
                    return result
            k += 1
    else:
        step = rule.interval * (12 if rule.freq == 'YEARLY' else 1)
        month_delta = (lower.year-a.year)*12+lower.month-a.month
        k = max(0, month_delta//step)
        rank = 0
        if rule.count is not None and k:
            if not rule.bymonthday:
                rank = k
            else:
                period=4800//gcd(step,4800)
                # Count only history that actually exists; share the pattern
                # across COUNT/UNTIL variants, which do not change month days.
                prefix = _month_prefix(a,step,rule.bymonthday,min(k,period))
                cycles, remainder = divmod(k,period)
                rank = cycles*prefix[-1]+prefix[remainder]
                rank -= sum(day<a.day for day in rrule._month_days(a.year,a.month,rule,a.day))
        while True:
            year, month = rrule._shift_month(a.year,a.month,k*step)
            if year>9999 or date(year,month,1)>end:
                break
            for day in rrule._month_days(year,month,rule,a.day):
                value = date(year,month,day)
                if value<a:
                    continue
                rank += 1
                if not append(value,rank):
                    return result
            k += 1
    return result
