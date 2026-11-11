/**
 * API 客户端 —— 前端不写任何 URL。
 *
 * 启动时拉一次 /api/v1/meta/features，得到「功能名 → 方法 + 路径模板」，
 * 之后只调 call('task.create', {...})。后端加接口前端零改动，也不会因为手抄
 * 路径抄错而 404。这是 R4「接口即唯一入口」在前端的体现。
 */

export class ApiError extends Error {
  constructor(code, message, detail, status) {
    super(message || code);
    this.code = code;
    this.detail = detail || {};
    this.status = status || 0;
  }
}

let FEATURES = null;
let nativeSequence=0;
const nativeCalls=new Map();
if(globalThis.QingdanAndroid)globalThis.qingdanNativeResult=(id,json)=>{
  const pending=nativeCalls.get(id);if(!pending)return;
  nativeCalls.delete(id);clearTimeout(pending.timer);
  try{const body=JSON.parse(json);if(body.ok)pending.resolve(body.data);else pending.reject(new ApiError(body.error.code,body.error.message,body.error.detail,0));}catch(error){pending.reject(error);}
};
function nativeCall(name,params){return new Promise((resolve,reject)=>{
  const id=String(++nativeSequence),timer=setTimeout(()=>{nativeCalls.delete(id);reject(new ApiError('TIMEOUT','Android operation timed out'));},120000);
  nativeCalls.set(id,{resolve,reject,timer});
  try{globalThis.QingdanAndroid.invoke(id,name,JSON.stringify(params));}catch(error){clearTimeout(timer);nativeCalls.delete(id);reject(error);}
});}

export async function loadFeatures() {
  if(globalThis.QingdanAndroid){FEATURES=await nativeCall('meta.features',{});return FEATURES;}
  const res = await fetch('/api/v1/meta/features');
  const body = await res.json().catch(() => null);
  if (!res.ok || !body || body.ok !== true) {
    throw new ApiError('BOOTSTRAP_FAILED', '无法读取接口清单，后端可能未启动', {}, res.status);
  }
  FEATURES = body.data;
  return FEATURES;
}

export function hasFeature(name) {
  return !!(FEATURES && FEATURES[name]);
}

/** 页脚显示的接口数量必须来自实际注册表，不能写死 —— 加一个接口就过期。 */
export function featureCount() { return FEATURES ? Object.keys(FEATURES).length : 0; }

/** 把参数拆成 路径参数 / query / body。 */
function splitArgs(f, params) {
  const [method, tpl] = f.http;
  const names = [...tpl.matchAll(/\{(\w+)\}/g)].map((m) => m[1]);
  const rest = { ...params };
  const path = tpl.replace(/\{(\w+)\}/g, (_, k) => {
    const v = rest[k];
    delete rest[k];
    if (v === undefined || v === null || v === '') {
      throw new ApiError('VALIDATION_ERROR', `缺少路径参数 ${k}`, {}, 422);
    }
    return encodeURIComponent(v);
  });
  const keys = Object.keys(rest).filter((k) => rest[k] !== undefined);
  return { method, path, rest, keys };
}

export async function call(feature, params = {}) {
  if (!FEATURES) throw new ApiError('NOT_LOADED', 'api.js 尚未 loadFeatures()');
  const f = FEATURES[feature];
  if (!f) throw new ApiError('NO_SUCH_FEATURE', `后端没有这个功能：${feature}`, {}, 404);
  if(globalThis.QingdanAndroid){const data=await nativeCall(feature,params);if(feature==='data.export'&&data.path)globalThis.QingdanAndroid.shareFile(data.path);return data;}

  const { method, path, rest, keys } = splitArgs(f, params);
  let url = path;
  const init = { method, headers: {} };

  if (method === 'GET' || method === 'DELETE') {
    // query 只能传字符串；null 表示「用后端默认值」，直接不发这个参数
    const qs = keys.filter((k) => rest[k] !== null)
      .map((k) => `${encodeURIComponent(k)}=${encodeURIComponent(
        typeof rest[k] === 'object' ? JSON.stringify(rest[k]) : rest[k])}`);
    if (qs.length) url += '?' + qs.join('&');
  } else {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(rest);
  }

  let res, body;
  try {
    res = await fetch(url, init);
    body = await res.json().catch(() => null);
  } catch (e) {
    throw new ApiError('NETWORK', `无法连接本地服务：${e.message}`, {}, 0);
  }
  if (body && body.ok === true) return body.data;
  const err = (body && body.error) || { code: 'UNKNOWN', message: `HTTP ${res.status}` };
  throw new ApiError(err.code, err.message, err.detail, res.status);
}
