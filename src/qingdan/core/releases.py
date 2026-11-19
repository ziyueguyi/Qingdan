"""Explicit, bounded release checks; never download or install executables."""
from __future__ import annotations

import json
from importlib.resources import files
import re
import time

import httpx

from qingdan import __version__
from qingdan.core.registry import feature, validation_error

RELEASES_URL = 'https://github.com/ziyueguyi/Qingdan/releases'
LATEST_URL = 'https://api.github.com/repos/ziyueguyi/Qingdan/releases/latest'
MAX_RESPONSE = 1024 * 1024
TOTAL_TIMEOUT = 12
_VERSION_PART = r'(0|[1-9]\d{0,4})'


def _version(value):
    if not isinstance(value,str) or not re.fullmatch(rf'v?{_VERSION_PART}\.{_VERSION_PART}\.{_VERSION_PART}',value):
        raise ValueError('unsupported release version')
    return tuple(int(part) for part in value.removeprefix('v').split('.'))


def _latest() -> dict:
    deadline=time.monotonic()+TOTAL_TIMEOUT
    # Per-operation timeouts bound each socket wait; the deadline also stops a
    # peer that keeps the response alive forever by trickling tiny chunks.
    timeout=httpx.Timeout(3.0,connect=5.0)
    with httpx.Client(timeout=timeout,trust_env=True,follow_redirects=False) as client:
        with client.stream('GET',LATEST_URL,headers={
            'Accept':'application/vnd.github+json','User-Agent':'Qingdan/'+__version__}) as response:
            if response.status_code in (403,429):return {'status':'failed','reason':'rate_limit'}
            if response.status_code!=200:return {'status':'failed','reason':'http'}
            content=bytearray()
            for chunk in response.iter_bytes():
                if time.monotonic()>deadline:raise httpx.TimeoutException('release check deadline exceeded')
                content.extend(chunk)
                if len(content)>MAX_RESPONSE:raise ValueError('release response too large')
    release=json.loads(content)
    if not isinstance(release,dict) or release.get('draft') is not False or release.get('prerelease') is not False:
        raise ValueError('not a stable release')
    tag=release.get('tag_name')
    latest=_version(tag)
    body=release.get('body') or ''
    if not isinstance(body,str):raise ValueError('invalid release notes')
    return {'status':'available' if latest>_version(__version__) else 'current',
            'latest_version':tag.removeprefix('v'),'latest_notes':body[:20000]}


@feature(name='app.release_info',group='app',http=('POST','/api/v1/app/release'),
         idempotent=True,manages_lock=True)
def release_info(check: bool = False, lang: str = 'en') -> dict:
    """Return bundled notes offline; contact GitHub only for an explicit check."""
    if lang not in {'zh','en'}:raise validation_error('unsupported language')
    bundled=json.loads(files('qingdan.core').joinpath('release-notes.json').read_text(encoding='utf-8'))
    result={'current_version':__version__,'current_notes':bundled.get(__version__,{}).get(lang,''),'lang':lang,
            'releases_url':RELEASES_URL,'status':'unchecked','latest_version':None,'latest_notes':''}
    if check:
        try:result.update(_latest())
        except httpx.HTTPError:result.update(status='failed',reason='network')
        except (ValueError,TypeError,KeyError):result.update(status='failed',reason='invalid')
    return result
