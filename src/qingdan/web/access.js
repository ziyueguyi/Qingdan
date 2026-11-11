/** Local-only API/CLI administration. Raw keys stay in this view's memory only. */
import { call } from './api.js';
import { t, locale } from './i18n.js';
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const A = {config:null,tokens:[],total:0,offset:0,audit:[],auditTotal:0,auditOffset:0,catalog:null,raw:null,editor:null,tab:'remote',inline:false};
let refresh = () => {}, report = () => {};
const permissionKeys = {read:'api_perm_read',create:'api_perm_create',update:'api_perm_update',delete:'api_perm_delete',execute:'api_perm_execute'};
const permissionText = p => t(permissionKeys[p] || p);
const dateText = value => value ? new Date(value).toLocaleString(locale()) : t('api_never');
function inputDate(value) {
  const d=new Date(value),p=n=>String(n).padStart(2,'0');
  return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}
export function configureAccessUI(onRefresh,onError) { refresh=onRefresh;report=onError; }
export function clearAccessSecret() { A.raw=null; }
export async function loadAccess() {
  const [config,tokens,audit,catalog]=await Promise.all([
    call('access.config_get'),call('access.token_list',{limit:5,offset:A.offset,include_revoked:true}),
    call('access.audit',{limit:5,offset:A.auditOffset}), A.catalog ? Promise.resolve(A.catalog) : call('access.catalog')]);
  Object.assign(A,{config,tokens:tokens.items,total:tokens.total,audit:audit.items,auditTotal:audit.total,catalog});
}

export function renderCliTab() {
  return `<section class="api-section"><h3>${esc(t('api_cli_title'))}</h3><p class="desc">${esc(t('api_cli_local'))}</p><code class="api-code">QingdanCLI.exe task list</code><code class="api-code">QingdanCLI.exe task create --title Task</code><code class="api-code">QingdanCLI.exe autosync config_get</code><code class="api-code">QingdanCLI.exe autosync config_set --enabled true --mode daily --times '["09:00","18:00"]'</code><p class="desc">${esc(t('api_local_channel'))}: ${esc(A.config?.local_url || '127.0.0.1')}</p></section>`;
}
function pager(prefix,offset,total) {
  return `<div class="picker-pages"><button class="iconbtn" data-act="${prefix}-prev" ${offset===0?'disabled':''}>‹</button><span class="desc">${total ? Math.floor(offset/5)+1 : 0} / ${Math.ceil(total/5)}</span><button class="iconbtn" data-act="${prefix}-next" ${offset+5>=total?'disabled':''}>›</button></div>`;
}
export function renderAccessTab() {
  if(!A.config)return `<div class="desc">${esc(t('api_loading'))}</div>`;
  const c=A.config.config,r=A.config.runtime;
  const host=['0.0.0.0','::'].includes(c.host)?t('api_this_pc_ip'):(c.host.includes(':')?`[${c.host}]`:c.host);
  const url=`${c.transport==='both'?'https':c.transport}://${host}:${c.port}`;
  const rows=A.tokens.map(key=>`<tr><td><b>${esc(key.name)}</b><code class="desc">qd_${esc(key.id.slice(0,8))}…</code></td><td>${key.call_count}</td><td>${esc(dateText(key.expires_at_ms))}</td><td>${key.max_qps}</td><td>${key.max_calls??esc(t('api_unlimited'))}</td><td>${esc(dateText(key.created_at_ms))}</td><td>${key.last_used_ms?esc(dateText(key.last_used_ms)):'—'}</td><td>${esc(key.revoked?t('api_revoked'):key.expired?t('api_expired'):key.max_calls!==null&&key.call_count>=key.max_calls?t('api_quota_reached'):t('api_active'))}</td><td>${esc(key.note||'—')}</td><td><div class="btnrow"><button class="btn" data-act="access-token-edit" data-id="${esc(key.id)}" ${key.revoked?'disabled':''}>${esc(t('api_edit_permissions'))}</button><button class="btn danger" data-act="access-token-revoke" data-id="${esc(key.id)}" ${key.revoked?'disabled':''}>${esc(t('api_revoke'))}</button></div></td></tr>`).join('');
  const audit=A.audit.map(row=>`<tr><td>${esc(dateText(row.at))}</td><td>${esc(row.token_name || '—')}<br><span class="desc">${esc(row.client_ip || '—')}</span></td><td>${esc(row.feature || '—')}</td><td class="${row.status>=400?'api-bad':''}">${row.status}</td></tr>`).join('');
  const content=`<section class="api-section" data-access-section="remote"><h3>${esc(t('api_remote_title'))}<span class="api-status ${r.running?'ok':''}">${esc(t(r.running?'api_running':'api_stopped'))}</span></h3>
    ${r.error_code?`<div class="hint api-bad">${esc(t('api_runtime_error'))} ${esc(r.error_code)}</div>`:''}
    <div class="field api-enable-row"><label for="api-enabled">${esc(t('api_enable'))}</label><input id="api-enabled" class="api-switch" type="checkbox" ${c.enabled?'checked':''}></div>
    <div class="api-config-grid">
    <div class="field"><label>${esc(t('api_bind'))}</label><input class="ctl" id="api-host" value="${esc(c.host)}" required></div>
    <div class="field"><label>${esc(t('api_port'))}</label><input class="ctl" id="api-port" type="number" min="1" max="65535" value="${c.port}" required></div>
    <div class="field"><label>${esc(t('api_transport'))}</label><select class="ctl" id="api-transport"><option value="http" ${c.transport==='http'?'selected':''}>${esc(t('api_http_plain'))}</option><option value="https" ${c.transport==='https'?'selected':''}>HTTPS</option><option value="both" ${c.transport==='both'?'selected':''}>HTTP + HTTPS</option></select></div>
    <div class="field" id="api-http-port-row" ${c.transport!=='both'?'hidden':''}><label>${esc(t('api_http_port'))}</label><input class="ctl" id="api-http-port" type="number" min="1" max="65535" value="${c.http_port}"></div>
    <div id="api-tls-fields" ${c.transport==='http'?'hidden':''}><div class="field"><label>${esc(t('api_certificate_mode'))}</label><select class="ctl" id="api-auto-tls"><option value="true" ${c.auto_tls?'selected':''}>${esc(t('api_auto_cert'))}</option><option value="false" ${!c.auto_tls?'selected':''}>${esc(t('api_import_cert'))}</option></select></div><p class="desc">${esc(t('api_auto_cert_note'))}</p><div id="api-manual-tls" ${c.auto_tls?'hidden':''}><div class="field"><label>${esc(t('api_cert'))}</label><input class="ctl" id="api-cert" value="${esc(c.tls_cert)}" placeholder="server-cert.pem"></div><div class="field"><label>${esc(t('api_key'))}</label><input class="ctl" id="api-key" value="${esc(c.tls_key)}" placeholder="server-key.pem"></div></div></div></div>
    <p class="desc" id="api-http-note" ${c.transport==='https'?'hidden':''}>${esc(t('api_http_note'))}</p>
    <details><summary>${esc(t('api_limits'))}</summary><div class="field"><label>${esc(t('api_rate'))}</label><input class="ctl" id="api-rate" type="number" min="10" max="6000" value="${c.requests_per_minute}"></div><div class="field"><label>${esc(t('api_body_limit'))}</label><input class="ctl" id="api-body" type="number" min="1" max="32768" value="${c.max_body_kb}"></div></details>
    <div class="btnrow"><button class="btn pri" data-act="access-save">${esc(t('save'))}</button></div>
    <details class="api-connection-help"><summary>${esc(t('api_call_example'))}</summary><p class="desc">${esc(t('api_remote_note'))}</p><code class="api-code">${esc(url)}/api/v1/tasks</code><code class="api-code">Authorization: Bearer TOKEN</code></details>
  </section>
  <section class="api-section" data-access-section="tokens"><h3>${esc(t('api_tokens_title'))}<button class="btn" data-act="access-token-new">＋ ${esc(t('api_create_token'))}</button></h3>
    ${A.raw?`<div class="api-secret"><b>${esc(t('api_once'))}</b><input class="ctl" id="api-new-token" readonly value="${esc(A.raw)}"><div class="btnrow"><button class="btn" data-act="access-copy">${esc(t('api_copy'))}</button><button class="btn" data-act="access-token-hide">${esc(t('api_hide'))}</button></div></div>`:''}
    ${rows?`<div class="api-table-wrap"><table class="api-table api-tokens-table"><thead><tr>${['api_token_col','api_call_stats','api_expires','api_qps','api_call_limit','api_created','api_recent','api_status_col','api_note','api_actions'].map(k=>`<th>${esc(t(k))}</th>`).join('')}</tr></thead><tbody>${rows}</tbody></table></div>`:`<p class="desc">${esc(t('api_no_tokens'))}</p>`}${pager('access-tokens',A.offset,A.total)}
    <p class="desc">${esc(t('api_scope_note'))}</p>
  </section>
  <section class="api-section" data-access-section="logs"><h3>${esc(t('api_audit_title'))}<button class="btn" data-act="access-refresh">${esc(t('api_refresh'))}</button></h3><div class="api-table-wrap"><table class="api-table"><thead><tr><th>${esc(t('api_time'))}</th><th>${esc(t('api_caller'))}</th><th>${esc(t('api_feature'))}</th><th>HTTP</th></tr></thead><tbody>${audit}</tbody></table></div>${pager('access-audit',A.auditOffset,A.auditTotal)}</section>`;
  const container=document.createElement('div');container.innerHTML=content;
  const nav=`<div class="api-subtabs" role="navigation">${[['remote','api_remote_title'],['tokens','api_tokens_title'],['create','api_create_token'],['logs','api_audit_title']].map(([key,label])=>`<button class="btn ${A.tab===key?'pri':''}" data-act="access-tab" data-tab="${key}" aria-current="${A.tab===key?'page':'false'}">${esc(t(label))}</button>`).join('')}</div>`;
  if(A.tab==='create'){
    queueMicrotask(()=>{if($('api-inline-editor'))openEditor(null,true);});
    const secret=A.raw?container.querySelector('.api-secret')?.outerHTML||'':'';
    return nav+secret+'<section class="api-section" id="api-inline-editor"></section>';
  }
  return nav+(container.querySelector(`[data-access-section="${A.tab}"]`)?.outerHTML||'');
}

function renderFeatureRows() {
  const e=A.editor;if(!e)return;
  const items=A.catalog.items.filter(x=>x.remote_allowed && (!e.query || (x.name+' '+x.http[1]).toLowerCase().includes(e.query.toLowerCase())));
  const page=items.slice(e.page*10,e.page*10+10);
  $('api-feature-rows').innerHTML=page.map(f=>`<label class="api-feature-row"><input type="checkbox" data-access-feature="${esc(f.name)}" ${e.features.has(f.name)?'checked':''}><span><b>${esc(f.name)}</b><code>${esc(f.http.join(' '))}</code></span><span class="desc">${esc(f.permissions.map(permissionText).join(' / '))}</span></label>`).join('');
  $('api-feature-page').textContent=`${items.length ? e.page+1 : 0} / ${Math.ceil(items.length/10)}`;
  $('api-feature-prev').disabled=e.page===0;
  $('api-feature-next').disabled=(e.page+1)*10>=items.length;
}

function openEditor(id,inline=false) {
  let dialog=$('api-token-dialog');
  if(dialog?.open)return;
  const item=id?A.tokens.find(x=>x.id===id):null;
  A.editor={id:id||null,permissions:new Set(item?.permissions || ['read']),
            features:new Set(item?.features.filter(x=>x!=='*') || []),all:!item||item.features.includes('*'),page:0,query:''};
  A.inline=inline;
  if(inline)dialog=$('api-inline-editor');
  else if(!dialog){dialog=document.createElement('dialog');dialog.id='api-token-dialog';dialog.className='api-token-dialog';document.body.append(dialog);dialog.addEventListener('cancel',()=>{A.editor=null;});}
  dialog.setAttribute('aria-label',t(id?'api_edit_permissions':'api_create_token'));
  dialog.innerHTML=`<div class="mh">${esc(t(id?'api_edit_permissions':'api_create_token'))}<button class="iconbtn" data-act="access-editor-cancel">✕</button></div><div class="mb">
    <div class="field"><label>${esc(t('api_token_name'))}</label><input class="ctl" id="api-token-name" maxlength="100" value="${esc(item?.name||'')}" required></div>
    <div class="field"><label>${esc(t('api_qps'))}</label><input class="ctl" type="number" id="api-token-qps" min="1" max="1000" value="${item?.max_qps||10}"></div>
    <div class="field"><label>${esc(t('api_call_limit'))}</label><input class="ctl" type="number" id="api-token-quota" min="1" max="1000000000" value="${item?.max_calls??''}" placeholder="${esc(t('api_unlimited'))}"></div>
    <div class="field"><label>${esc(t('api_note'))}</label><input class="ctl" id="api-token-note" maxlength="500" value="${esc(item?.note||'')}"></div>
    <div class="field"><label>${esc(t('api_permissions'))}</label><div class="api-permissions">${A.catalog.permissions.map(p=>`<label class="chk"><input type="checkbox" data-access-permission="${p}" ${A.editor.permissions.has(p)?'checked':''}>${esc(permissionText(p))}</label>`).join('')}</div></div>
    <div class="field"><label>${esc(t('api_expires'))}</label><input class="ctl" type="datetime-local" id="api-token-expiry" value="${inputDate(item?.expires_at_ms || Date.now()+30*86400000)}"></div>
    <label class="chk"><input type="checkbox" id="api-token-never" ${item&&item.expires_at_ms===null?'checked':''}>${esc(t('api_never'))}</label>
    <div class="field"><label>${esc(t('api_feature_scope'))}</label><select class="ctl" id="api-token-scope"><option value="all" ${A.editor.all?'selected':''}>${esc(t('api_all_business'))}</option><option value="selected" ${!A.editor.all?'selected':''}>${esc(t('api_selected_features'))}</option></select></div>
    <div id="api-feature-picker" ${A.editor.all?'hidden':''}><input class="ctl" type="search" id="api-feature-search" placeholder="${esc(t('api_search_features'))}"><div id="api-feature-rows"></div><div class="picker-pages"><button class="iconbtn" id="api-feature-prev" data-act="access-features-prev">‹</button><span class="desc" id="api-feature-page"></span><button class="iconbtn" id="api-feature-next" data-act="access-features-next">›</button></div></div>
    <p class="desc">${esc(t('api_scope_note'))}</p><div id="api-editor-error" class="hint api-bad" hidden role="alert"></div><div class="btnrow"><button class="btn" data-act="access-editor-cancel">${esc(t('cancel'))}</button><button class="btn pri" data-act="access-editor-save">${esc(t('save'))}</button></div></div>`;
  $('api-token-scope').onchange=()=>{A.editor.all=$('api-token-scope').value==='all';$('api-feature-picker').hidden=A.editor.all;};
  $('api-feature-search').oninput=()=>{A.editor.query=$('api-feature-search').value.trim();A.editor.page=0;renderFeatureRows();};
  $('api-token-never').onchange=()=>{$('api-token-expiry').disabled=$('api-token-never').checked;};
  $('api-token-expiry').disabled=$('api-token-never').checked;
  renderFeatureRows();if(!inline)dialog.showModal();$('api-token-name').focus();
}

async function saveEditor(button) {
  if(!$('api-token-name').reportValidity())return;
  const e=A.editor,never=$('api-token-never').checked;
  const params={name:$('api-token-name').value.trim(),permissions:[...e.permissions],features:e.all?['*']:[...e.features],never_expires:never};
  if(!$('api-token-qps').reportValidity()||!$('api-token-quota').reportValidity())return;
  params.max_qps=Number($('api-token-qps').value);params.max_calls=$('api-token-quota').value?Number($('api-token-quota').value):null;params.note=$('api-token-note').value.trim();
  if(e.id)params.unlimited_calls=params.max_calls===null;
  if(!params.permissions.length)throw new Error(t('api_need_permission'));
  if(!params.features.length)throw new Error(t('api_need_feature'));
  if(!never){const value=new Date($('api-token-expiry').value);if(!Number.isFinite(value.getTime()))throw new Error(t('api_need_expiry'));params.expires_at=value.toISOString();}
  button.disabled=true;
  try{
    if(e.id)await call('access.token_update',{token_id:e.id,...params});
    else {const result=await call('access.token_create',params);A.raw=result.token;A.offset=0;}
    if(!A.inline)$('api-token-dialog').close();A.editor=null;await loadAccess();refresh();
  }finally{button.disabled=false;}
}

export function handleAccessChange(el) {
  if(el.dataset.accessPermission && A.editor){const p=el.dataset.accessPermission;el.checked?A.editor.permissions.add(p):A.editor.permissions.delete(p);return true;}
  if(el.dataset.accessFeature && A.editor){const f=el.dataset.accessFeature;el.checked?A.editor.features.add(f):A.editor.features.delete(f);return true;}
  if(el.id==='api-transport'){$('api-tls-fields').hidden=el.value==='http';$('api-http-note').hidden=el.value==='https';$('api-http-port-row').hidden=el.value!=='both';return true;}
  if(el.id==='api-auto-tls'){$('api-manual-tls').hidden=el.value==='true';return true;}
  return false;
}

export async function handleAccessAction(el) {
  const a=el.dataset.act,id=el.dataset.id;
  if(a==='access-tab'){A.tab=el.dataset.tab;A.raw=null;A.editor=null;refresh();return;}
  if(a==='access-editor-save'){
    try{await saveEditor(el);}catch(error){$('api-editor-error').hidden=false;$('api-editor-error').textContent=error.message;}
    return;
  }
  if(a==='access-editor-cancel'){if(A.inline){A.tab='tokens';refresh();}else $('api-token-dialog').close();A.editor=null;return;}
  if(a==='access-token-new'){A.tab='create';refresh();return;}
  if(a==='access-token-edit'){openEditor(id);return;}
  if(a==='access-features-prev'||a==='access-features-next'){A.editor.page+=a.endsWith('prev')?-1:1;renderFeatureRows();return;}
  if(a==='access-copy'){
    try{await navigator.clipboard.writeText(A.raw || '');el.textContent=t('api_copied');}
    catch{$('api-new-token').focus();$('api-new-token').select();}
    return;
  }
  if(a==='access-token-hide'){A.raw=null;refresh();return;}
  if(a==='access-token-revoke'){
    if(!confirm(t('api_confirm_revoke')))return;
    await call('access.token_revoke',{token_id:id});
  }else if(a==='access-save'){
    for(const field of ['api-host','api-port','api-rate','api-body'])if(!$(field).reportValidity())return;
    await call('access.config_set',{config:{enabled:$('api-enabled').checked,host:$('api-host').value.trim(),port:Number($('api-port').value),http_port:Number($('api-http-port').value),auto_tls:$('api-auto-tls').value==='true',transport:$('api-transport').value,tls_cert:$('api-cert').value.trim(),tls_key:$('api-key').value.trim(),requests_per_minute:Number($('api-rate').value),max_body_kb:Number($('api-body').value)}});
  }else if(a==='access-tokens-prev'||a==='access-tokens-next')A.offset=Math.max(0,A.offset+(a.endsWith('prev')?-5:5));
  else if(a==='access-audit-prev'||a==='access-audit-next')A.auditOffset=Math.max(0,A.auditOffset+(a.endsWith('prev')?-5:5));
  await loadAccess();refresh();
}
