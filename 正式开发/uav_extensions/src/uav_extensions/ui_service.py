"""通用前端 UI 服务（docs/08 第一层）——面向任意宿主的业务视图页。

两类页面（自包含 HTML+Canvas，无 CDN、无登录，token 即能力）：
- /ui/approval/{action_id}?t=<page_token>  确认卡片页：渲染确认单 → 人点确认 →
  展示 [SYSTEM_CONFIRMATION] 一行由用户带回对话（人在环通用化，任何宿主零集成）
- /ui/view/{vtoken}                         通用视图页：trajectory/map 快照落图

敏感凭证边界：浏览器只见 page_token/vtoken；审批的 admin key 与服务间
X-API-Key 全部留在本服务后端代理层。

对接：
  mcp-services → POST /ui/api/view（X-API-Key）注册几何快照拿 view_url；
  浏览器 → GET 页面 + /ui/api/*（页面 token 门禁）。

运行：python -m uav_extensions.ui_service   # 默认 0.0.0.0:8213
环境：APPROVAL_BASE（默认 http://127.0.0.1:8205）、UAV_MCP_API_KEY、
      UI_PORT、UI_PUBLIC_BASE（生成 view_url 用，默认 http://127.0.0.1:8213）
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import time
from typing import Any

import httpx
import uvicorn
from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)

APPROVAL_BASE = os.getenv("APPROVAL_BASE", "http://127.0.0.1:8205").rstrip("/")
API_KEY = os.getenv("UAV_MCP_API_KEY", "").strip()
PUBLIC_BASE = os.getenv("UI_PUBLIC_BASE", "http://127.0.0.1:8213").rstrip("/")
VIEW_TTL_S = 1800

app = FastAPI(title="UAV 通用前端 UI 服务", version="0.1.0")

_views: dict[str, dict[str, Any]] = {}  # vtoken -> {type, payload, expires_at}


def _gc_views() -> None:
    now = time.time()
    for k in [k for k, v in _views.items() if v["expires_at"] < now]:
        _views.pop(k, None)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


# ── 视图快照（mcp-services 注册 → 页面渲染）──────────────────

@app.post("/ui/api/view")
def register_view(body: dict[str, Any] = Body(...),
                  x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    if API_KEY and not hmac.compare_digest(x_api_key or "", API_KEY):
        raise HTTPException(401, "invalid X-API-Key")
    vtype = body.get("type")
    if vtype not in ("trajectory", "map"):
        raise HTTPException(422, "type 须为 trajectory / map")
    _gc_views()
    vtoken = secrets.token_urlsafe(18)
    _views[vtoken] = {"type": vtype, "title": body.get("title") or "",
                      "payload": body.get("payload") or {},
                      "expires_at": time.time() + VIEW_TTL_S}
    return {"vtoken": vtoken, "view_url": f"{PUBLIC_BASE}/ui/view/{vtoken}",
            "expires_in_s": VIEW_TTL_S}


@app.get("/ui/api/view/{vtoken}")
def view_data(vtoken: str) -> dict[str, Any]:
    v = _views.get(vtoken)
    if not v or v["expires_at"] < time.time():
        raise HTTPException(404, "视图不存在或已过期")
    return {k: v[k] for k in ("type", "title", "payload")}


# ── 确认卡片代理（admin key 不出后端）────────────────────────

async def _approval(method: str, path: str) -> Any:
    async with httpx.AsyncClient(timeout=8) as c:
        r = await c.request(method, f"{APPROVAL_BASE}{path}")
    if r.status_code >= 400:
        detail = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise HTTPException(r.status_code, detail)
    return r.json()


@app.get("/ui/api/approval/{action_id}")
async def approval_detail(action_id: str, t: str) -> Any:
    return await _approval("GET", f"/api/approval/{action_id}/page?t={t}")


@app.post("/ui/api/approval/{action_id}/approve")
async def approval_approve(action_id: str, t: str, u: str | None = None) -> Any:
    # u = 审批人身份（docs/09 阶段1，来自签名链接绑定的用户）
    suffix = f"&u={u}" if u else ""
    return await _approval("POST", f"/api/approval/{action_id}/approve-by-page?t={t}{suffix}")


@app.post("/ui/api/approval/{action_id}/cancel")
async def approval_cancel(action_id: str, t: str) -> Any:
    return await _approval("POST", f"/api/approval/{action_id}/cancel-by-page?t={t}")


# ── 页面（自包含，无外部依赖）────────────────────────────────

_BASE_CSS = """
body{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;margin:0;
     background:#f5f6f8;color:#1f2329}
.card{max-width:560px;margin:40px auto;background:#fff;border-radius:12px;
      box-shadow:0 2px 12px rgba(0,0,0,.08);padding:28px}
h1{font-size:18px;margin:0 0 4px}.sub{color:#8a919f;font-size:13px;margin-bottom:18px}
table{width:100%;border-collapse:collapse;font-size:14px;margin:12px 0}
td{padding:8px 6px;border-bottom:1px solid #eef0f3}td:first-child{color:#8a919f;width:96px}
.btn{display:inline-block;border:0;border-radius:8px;padding:10px 22px;font-size:15px;
     cursor:pointer;margin-right:10px}
.ok{background:#1456f0;color:#fff}.no{background:#eef0f3;color:#1f2329}
.badge{display:inline-block;font-size:12px;border-radius:4px;padding:2px 8px;margin-left:8px}
.b-pending{background:#fff3e0;color:#b26a00}.b-approved{background:#e8f5e9;color:#1b7a2f}
.b-other{background:#eef0f3;color:#8a919f}
.tokenline{background:#0f1720;color:#7ee787;font-family:ui-monospace,monospace;font-size:13px;
           padding:12px;border-radius:8px;word-break:break-all;margin:14px 0;user-select:all}
.hint{font-size:13px;color:#8a919f;line-height:1.7}
#msg{color:#c62828;font-size:13px;min-height:18px}
"""

_APPROVAL_HTML = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>无人机高危操作确认</title><style>{css}</style></head><body>
<div class="card">
  <h1>高危操作确认单 <span id="status" class="badge b-other">加载中</span></h1>
  <div class="sub" id="aid"></div>
  <div class="sub" id="who"></div>
  <table id="rows"></table>
  <div id="msg"></div>
  <div id="actions" style="display:none">
    <button class="btn ok" onclick="approve()">确认执行</button>
    <button class="btn no" onclick="cancelIt()">取消</button>
    <div class="hint" style="margin-top:12px">确认前请逐行核对上表内容。确认即授权执行本单锁定的动作与参数。</div>
  </div>
  <div id="done" style="display:none">
    <div class="hint"><b>已确认。</b>请复制下面整行内容，回到对话粘贴发送给助手（10 分钟内有效、一次性）：</div>
    <div class="tokenline" id="tok"></div>
  </div>
</div>
<script>
const q = new URLSearchParams(location.search), t = q.get('t'), u = q.get('u')||'';
const aid = location.pathname.split('/').pop();
const api = p => {{
  const uparam = p.includes('approve') && u ? `&u=${{encodeURIComponent(u)}}` : '';
  return fetch(`/ui/api/approval/${{aid}}${{p}}?t=${{encodeURIComponent(t)}}${{uparam}}`, p.includes('approve')||p.includes('cancel')?{{method:'POST'}}:undefined).then(async r=>{{if(!r.ok) throw new Error((await r.json()).detail||r.status); return r.json()}});
}};
function render(d){{
  document.getElementById('aid').textContent = `${{d.action_id}} · ${{d.action}}`;
  const parts = [];
  if(d.initiated_by) parts.push(`发起人：${{d.initiated_by}}`);
  if(u) parts.push(`当前审批人：${{u}}`);
  if(d.confirmed_by) parts.push(`已由 ${{d.confirmed_by}} 确认`);
  document.getElementById('who').textContent = parts.join('　·　');
  const st = document.getElementById('status');
  st.textContent = {{pending:'待确认',approved:'已确认',consumed:'已执行',cancelled:'已取消',expired:'已过期'}}[d.status]||d.status;
  st.className = 'badge ' + (d.status==='pending'?'b-pending':d.status==='approved'?'b-approved':'b-other');
  const rows = (d.summary&&d.summary.rows)||[];
  document.getElementById('rows').innerHTML = rows.map(r=>`<tr><td>${{r[0]}}</td><td>${{r[1]}}</td></tr>`).join('');
  document.getElementById('actions').style.display = d.status==='pending'?'block':'none';
}}
async function load(){{ try{{ render(await api('')) }}catch(e){{ document.getElementById('msg').textContent='加载失败：'+e.message }} }}
async function approve(){{
  if(!confirm('确认执行该高危操作？')) return;
  try{{
    const d = await api('/approve');
    document.getElementById('actions').style.display='none';
    document.getElementById('done').style.display='block';
    document.getElementById('tok').textContent = `[SYSTEM_CONFIRMATION] action=${{d.action}} confirm_token=${{d.confirm_token}}`;
    load();
  }}catch(e){{ document.getElementById('msg').textContent='确认失败：'+e.message }}
}}
async function cancelIt(){{ try{{ await api('/cancel'); load() }}catch(e){{ document.getElementById('msg').textContent=e.message }} }}
load();
</script></body></html>"""

_VIEW_HTML = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>无人机业务视图</title><style>{css}
#cv{{width:100%;height:520px;background:#0b1220;border-radius:8px;display:block}}
.legend{{font-size:12px;color:#8a919f;margin-top:8px}}</style></head><body>
<div class="card" style="max-width:820px">
  <h1 id="title">视图</h1><div class="sub" id="meta"></div>
  <canvas id="cv"></canvas>
  <div class="legend">■ 图斑/围栏（多边形） ── 航线/轨迹（折线） ● 点位。视图 30 分钟有效。</div>
  <div id="msg"></div>
</div>
<script>
const vt = location.pathname.split('/').pop();
function project(bounds, w, h, pad){{
  const [minx,miny,maxx,maxy] = bounds, sx=(w-2*pad)/(maxx-minx||1e-9), sy=(h-2*pad)/(maxy-miny||1e-9), s=Math.min(sx,sy);
  return ([x,y]) => [pad+(x-minx)*s, h-pad-(y-miny)*s];
}}
function extend(b,[x,y]){{ b[0]=Math.min(b[0],x);b[1]=Math.min(b[1],y);b[2]=Math.max(b[2],x);b[3]=Math.max(b[3],y); }}
fetch('/ui/api/view/'+vt).then(async r=>{{ if(!r.ok) throw new Error((await r.json()).detail||r.status); return r.json() }}).then(d=>{{
  document.getElementById('title').textContent = d.title || ({{trajectory:'轨迹回放',map:'态势落图'}})[d.type] || '视图';
  const cv = document.getElementById('cv'); cv.width = cv.clientWidth*2; cv.height = 520*2;
  const ctx = cv.getContext('2d'); ctx.scale(2,2);
  const W = cv.clientWidth, H = 520;
  const p = d.payload||{{}}, polys = p.polygons||[], line = p.line||[], pts = p.points||[];
  let b=[Infinity,Infinity,-Infinity,-Infinity];
  polys.forEach(pg=>pg.forEach(c=>extend(b,c))); line.forEach(c=>extend(b,c)); pts.forEach(c=>extend(b,[c[0],c[1]]));
  if(b[0]===Infinity){{ document.getElementById('msg').textContent='无几何数据'; return; }}
  const prj = project(b, W, H, 24);
  polys.forEach(pg=>{{ ctx.beginPath(); pg.forEach((c,i)=>{{const [x,y]=prj(c); i?ctx.lineTo(x,y):ctx.moveTo(x,y)}});
    ctx.closePath(); ctx.fillStyle='rgba(255,99,71,.25)'; ctx.fill(); ctx.strokeStyle='#ff6347'; ctx.stroke(); }});
  if(line.length){{ ctx.beginPath(); line.forEach((c,i)=>{{const [x,y]=prj(c); i?ctx.lineTo(x,y):ctx.moveTo(x,y)}});
    ctx.strokeStyle='#4da3ff'; ctx.lineWidth=2; ctx.stroke();
    const [sx,sy]=prj(line[0]), [ex,ey]=prj(line[line.length-1]);
    ctx.fillStyle='#7ee787'; ctx.beginPath(); ctx.arc(sx,sy,5,0,7); ctx.fill();
    ctx.fillStyle='#ffd166'; ctx.beginPath(); ctx.arc(ex,ey,5,0,7); ctx.fill(); }}
  ctx.fillStyle='#fff'; pts.forEach(c=>{{ const [x,y]=prj(c); ctx.beginPath(); ctx.arc(x,y,4,0,7); ctx.fill();
    if(c[2]) {{ ctx.font='11px sans-serif'; ctx.fillText(c[2], x+7, y+4); }} }});
  document.getElementById('meta').textContent = `多边形 ${{polys.length}} · 折线点 ${{line.length}} · 点位 ${{pts.length}}`;
}}).catch(e=>{{ document.getElementById('msg').textContent = '加载失败：'+e.message }});
</script></body></html>"""


@app.get("/ui/approval/{action_id}", response_class=HTMLResponse)
def approval_page(action_id: str) -> str:  # noqa: ARG001 —— 前端从 path/query 自取
    return _APPROVAL_HTML.format(css=_BASE_CSS)


@app.get("/ui/view/{vtoken}", response_class=HTMLResponse)
def view_page(vtoken: str) -> str:  # noqa: ARG001
    return _VIEW_HTML.format(css=_BASE_CSS)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    uvicorn.run(app, host=os.getenv("UI_HOST", "0.0.0.0"),
                port=int(os.getenv("UI_PORT", "8213")), log_level="info")
