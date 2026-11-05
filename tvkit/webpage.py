"""The web page: three static files served from memory. The script builds every element with textContent / setAttribute (never innerHTML),
so text from a manifest, a feed or a server cannot inject markup; the Content-Security-Policy below allows no inline script at all."""

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; "
       "form-action 'none'; frame-ancestors 'none'; base-uri 'none'")

HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#060c18">
<title>TV Launcher</title>
<link rel="stylesheet" href="/app.css"></head>
<body><div id="app"><p class="muted pad">Loading...</p></div><div id="toast" role="status"></div>
<script src="/app.js"></script></body></html>
"""

CSS = r"""
:root{--bg:#060c18;--panel:#0d1830;--line:#1d2c52;--ink:#e8eaf0;--mute:#8a96b8;--amber:#ffd040;--blue:#183060;--red:#ff5566}
@media (prefers-color-scheme: light){:root{--bg:#eef1f8;--panel:#fff;--line:#cdd5e8;--ink:#101830;--mute:#56627f;--amber:#a86a00;--blue:#dbe6ff;--red:#c4283c}}
*{box-sizing:border-box}html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif;padding-bottom:env(safe-area-inset-bottom)}
.pad{padding:16px}.muted{color:var(--mute)}h1{font-size:20px;margin:0}h2{font-size:15px;color:var(--amber);text-transform:uppercase;letter-spacing:.06em;margin:22px 0 8px}
header{display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid var(--line)}
nav{display:flex;gap:4px;padding:8px 12px;border-bottom:1px solid var(--line);overflow-x:auto}
nav button{flex:1;min-width:64px;background:none;border:0;color:var(--mute);padding:10px 6px;border-radius:10px;font:inherit}
nav button[aria-current=true]{background:var(--blue);color:var(--amber);font-weight:600}
main{padding:12px 16px 40px;max-width:560px;margin:0 auto}
button,input,select,textarea{font:inherit;color:inherit}
.btn{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px;min-height:48px;width:100%}
.btn:active{background:var(--blue)}.btn.primary{background:var(--amber);color:#201400;border-color:var(--amber);font-weight:600}.btn.danger{color:var(--red)}
.btn.small{width:auto;min-height:36px;padding:6px 12px}
.pad-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:12px 0}
.pad-grid .btn{font-size:20px;min-height:58px}
.row{display:flex;gap:8px;align-items:center}.row>*{flex:1}.row>.fixed{flex:none;width:auto;padding-left:20px;padding-right:20px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:12px 14px;margin:10px 0}
.card b{display:block}.card small{display:block;color:var(--mute)}
.bar{height:6px;background:var(--line);border-radius:3px;overflow:hidden;margin-top:8px}.bar>i{display:block;height:100%;background:var(--amber)}
.field{margin:12px 0}.field label{display:block;margin-bottom:4px}.field small{display:block;color:var(--mute);margin-top:2px}
input[type=text],input[type=number],input[type=password],select,textarea{width:100%;background:var(--bg);border:1px solid var(--line);border-radius:10px;padding:10px;min-height:44px}
input[type=checkbox]{width:22px;height:22px;vertical-align:middle;margin-right:8px}
.pin{font-size:32px;letter-spacing:.3em;text-align:center}
#toast{position:fixed;left:16px;right:16px;bottom:calc(16px + env(safe-area-inset-bottom));background:var(--blue);border:1px solid var(--amber);border-radius:12px;padding:12px 14px;opacity:0;transform:translateY(8px);transition:.2s;pointer-events:none;text-align:center}
#toast.on{opacity:1;transform:none}
.mt8{margin-top:8px}.log{white-space:pre-wrap;font-size:12px;overflow:auto}
.badge{font-size:12px;border:1px solid var(--amber);color:var(--amber);border-radius:999px;padding:1px 8px;margin-left:6px}
"""

JS = r"""
'use strict';
const state = {tab: 'remote', status: null, apps: null, settings: null, devices: null, store: null, timer: null, session: null};
const app = document.getElementById('app');

function el(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') e.className = v;
    else if (k === 'style') Object.assign(e.style, v);
    else if (k === 'value' || k === 'checked') e[k] = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? '' : v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return e;
}
let toastTimer;
function toast(msg) { const t = document.getElementById('toast'); t.textContent = msg; t.className = 'on'; clearTimeout(toastTimer); toastTimer = setTimeout(() => t.className = '', 3200); }

async function api(path, body) {
  const opt = {credentials: 'same-origin', headers: {'X-TVL': '1'}};
  if (body !== undefined) { opt.method = 'POST'; opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
  let r;
  try { r = await fetch('/api' + path, opt); } catch (e) { toast('Cannot reach the TV'); throw e; }
  let data = {};
  try { data = await r.json(); } catch (e) {}
  if (r.status === 401) { state.session = {paired: false}; render(); throw new Error('unpaired'); }
  if (!r.ok) { const err = new Error(data.error || ('Error ' + r.status)); err.data = data; throw err; }
  return data;
}
const act = (fn) => async (ev) => { try { await fn(ev); } catch (e) { if (e.message !== 'unpaired') toast(e.message); } };

// ---- pairing ----
function pairView() {
  const pin = el('input', {type: 'text', class: 'pin', inputmode: 'numeric', autocomplete: 'one-time-code', maxlength: '6', placeholder: '------'});
  const name = el('input', {type: 'text', maxlength: '40', value: guessName()});
  return el('main', {},
    el('h1', {}, 'Pair this phone'),
    el('p', {class: 'muted'}, 'On the TV open REMOTE, then type the 6-digit code it shows.'),
    el('div', {class: 'field'}, el('label', {}, 'Code'), pin),
    el('div', {class: 'field'}, el('label', {}, 'Name of this phone'), name),
    el('button', {class: 'btn primary', onclick: act(async () => {
      await api('/pair', {pin: pin.value.trim(), name: name.value.trim()});
      state.session = null; await boot();
    })}, 'Pair'));
}
function guessName() { const p = navigator.userAgent || ''; return /iPhone/.test(p) ? 'iPhone' : /iPad/.test(p) ? 'iPad' : /Android/.test(p) ? 'Android phone' : 'Browser'; }

// ---- remote ----
function key(name) { return act(() => api('/key', {name})); }
function pad(label, name, cls) { const go = name === 'home' ? act(() => api('/home', {})) : key(name); return el('button', {class: 'btn ' + (cls || ''), onclick: go, 'aria-label': name}, label); }
function fmt(sec) { sec = Math.max(0, Math.floor(sec || 0)); const m = Math.floor(sec / 60), s = sec % 60; return m + ':' + String(s).padStart(2, '0'); }

function nowCard() {
  const st = state.status; if (!st) return el('div', {class: 'card muted'}, 'Reading the TV...');
  const a = st.app || st.launcher, p = a && a.playing;
  const run = st.launcher.running;
  const title = p ? p.title : (a && a.screen) || (run ? run.name : 'Menu');
  const sub = p ? [p.artist, p.album].filter(Boolean).join(' - ') : run ? run.name : 'Home menu';
  const kids = [el('small', {}, run ? run.name : 'Launcher'), el('b', {}, title), sub ? el('small', {}, sub) : null];
  if (p && p.dur) kids.push(el('div', {class: 'bar'}, el('i', {style: {width: Math.min(100, 100 * (p.pos || 0) / p.dur) + '%'}})), el('small', {}, fmt(p.pos) + ' / ' + fmt(p.dur) + (p.paused ? '  paused' : '')));
  return el('div', {class: 'card', id: 'now'}, ...kids);
}
function remoteView() {
  const text = el('input', {type: 'text', maxlength: '64', placeholder: 'Type here, then Send', autocapitalize: 'none', autocomplete: 'off'});
  const typing = state.status && ((state.status.app || state.status.launcher) || {}).text_input;
  return el('main', {}, nowCard(),
    el('div', {class: 'pad-grid'},
      pad('Back', 'back'), pad('▲', 'up'), pad('Home', 'home'),
      pad('◀', 'left'), pad('OK', 'enter', 'primary'), pad('▶', 'right'),
      pad('Info', 'info'), pad('▼', 'down'), pad('Menu', 'menu')),
    el('div', {class: 'row'}, pad('Vol −', 'voldown'), pad('Mute', 'mute'), pad('Vol +', 'volup')),
    el('h2', {}, typing ? 'Typing (a keyboard is open on the TV)' : 'Type text'),
    el('div', {class: 'row'}, text, el('button', {class: 'btn fixed primary', onclick: act(async () => { await api('/text', {text: text.value}); text.value = ''; })}, 'Send')));
}

// ---- apps ----
function appsView() {
  const apps = state.apps;
  if (!apps) return el('main', {}, el('p', {class: 'muted'}, 'Loading...'));
  const running = state.status && state.status.launcher.running;
  return el('main', {}, el('h2', {}, 'Apps'), ...apps.map(a => el('div', {class: 'card'},
    el('b', {}, a.name, running && running.id === a.id ? el('span', {class: 'badge'}, 'running') : null, a.hidden ? el('span', {class: 'badge'}, 'hidden') : null),
    el('small', {}, [a.desc, 'v' + a.version, a.source].filter(Boolean).join('  ·  ')),
    el('div', {class: 'row mt8'},
      el('button', {class: 'btn small primary', onclick: act(async () => { await api('/apps/start', {id: a.id}); toast('Starting ' + a.name); })}, 'Start'),
      el('button', {class: 'btn small', onclick: act(async () => {
        const hidden = new Set(state.apps.filter(x => x.hidden).map(x => x.id)); a.hidden ? hidden.delete(a.id) : hidden.add(a.id);
        await api('/settings', {changes: {'launcher.hidden': [...hidden]}}); await load('apps');
      })}, a.hidden ? 'Show in menu' : 'Hide from menu')))));
}

// ---- settings ----
function settingsView() {
  if (!state.settings) return el('main', {}, el('p', {class: 'muted'}, 'Loading...'));
  const {schema, values} = state.settings, inputs = {}, groups = {};
  for (const f of schema) (groups[f.group] = groups[f.group] || []).push(f);
  const appIds = (state.apps || []).map(a => a.id);
  const out = [];
  for (const [g, fields] of Object.entries(groups)) {
    out.push(el('h2', {}, g));
    for (const f of fields) {
      const v = values[f.key]; let input, get;
      if (f.type === 'bool') { input = el('input', {type: 'checkbox', checked: !!v}); get = () => input.checked; }
      else if (f.type === 'int') { input = el('input', {type: 'number', value: v, min: f.min, max: f.max, inputmode: 'numeric'}); get = () => parseInt(input.value, 10); }
      else if (f.type === 'app') { input = el('select', {}, el('option', {value: ''}, '(none)'), ...appIds.map(i => el('option', {value: i}, i))); input.value = v || ''; get = () => input.value || null; }
      else if (f.type === 'applist') { input = el('input', {type: 'text', value: (v || []).join(', ')}); get = () => input.value.split(',').map(s => s.trim()).filter(Boolean); }
      else if (f.type === 'paths') { input = el('textarea', {rows: '3'}); input.value = (v || []).join('\n'); get = () => input.value.split('\n').map(s => s.trim()).filter(Boolean); }
      else if (f.type === 'feeds' || f.type === 'repos') { input = el('textarea', {rows: '4', placeholder: 'Name | https://address'}); input.value = (v || []).map(x => x.name + ' | ' + x.url).join('\n');
        get = () => input.value.split('\n').map(s => s.trim()).filter(Boolean).map(l => { const i = l.indexOf('|'); return {name: l.slice(0, i).trim(), url: l.slice(i + 1).trim()}; }); }
      else { input = el('input', {type: 'text', value: v == null ? '' : v}); get = () => input.value; }
      inputs[f.key] = {get, orig: JSON.stringify(v == null ? (f.type === 'app' ? null : v) : v)};
      out.push(el('div', {class: 'field'}, f.type === 'bool' ? el('label', {}, input, f.label) : [el('label', {}, f.label), input], f.help ? el('small', {}, f.help) : null));
    }
  }
  return el('main', {}, ...out, el('button', {class: 'btn primary', onclick: act(async () => {
    const changes = {};
    for (const [k, i] of Object.entries(inputs)) { const now = i.get(); if (JSON.stringify(now) !== i.orig) changes[k] = now; }
    if (!Object.keys(changes).length) return toast('Nothing changed');
    const r = await api('/settings', {changes}); state.settings.values = r.values; toast('Saved. Apps pick it up when they next start.'); render();
  })}, 'Save'));
}

// ---- system ----
async function confirmOnTv(id, what) {
  toast('Look at the TV: press OK to ' + what);
  for (let i = 0; i < 35; i++) {
    await new Promise(r => setTimeout(r, 2000));
    let s; try { s = await api('/confirm/' + id); } catch (e) { return; }
    if (s.state === 'allowed') return toast('Done');
    if (s.state === 'denied') return toast('Cancelled on the TV');
    if (s.state === 'expired') return toast('Nobody answered on the TV');
  }
}
function systemView() {
  const devs = state.devices;
  return el('main', {},
    el('h2', {}, 'This device'),
    el('div', {class: 'row'},
      el('button', {class: 'btn', onclick: act(async () => confirmOnTv((await api('/system', {action: 'restart'})).confirm, 'restart'))}, 'Restart'),
      el('button', {class: 'btn danger', onclick: act(async () => confirmOnTv((await api('/system', {action: 'poweroff'})).confirm, 'shut down'))}, 'Shut down')),
    el('p', {class: 'muted'}, 'These ask for OK on the TV first, so nobody can switch it off from across the room by accident.'),
    el('h2', {}, 'Paired phones'),
    ...(devs ? devs.map(d => el('div', {class: 'card'}, el('b', {}, d.name, d.this ? el('span', {class: 'badge'}, 'this phone') : null),
      el('small', {}, 'last seen ' + new Date(d.last_seen * 1000).toLocaleString()),
      el('div', {class: 'mt8'}, el('button', {class: 'btn small danger', onclick: act(async () => { await api('/devices/revoke', {id: d.id}); if (d.this) { state.session = {paired: false}; render(); } else await load('devices'); })}, d.this ? 'Sign out this phone' : 'Remove')))) : [el('p', {class: 'muted'}, 'Loading...')]),
    el('h2', {}, 'Log'), el('pre', {class: 'card muted log'}, state.log || 'Loading...'));
}

// ---- store ----
async function watchJob(id, what) {
  toast('Working on it...');
  for (let i = 0; i < 90; i++) {
    await new Promise(r => setTimeout(r, 1500));
    let j; try { j = await api('/job/' + id); } catch (e) { return; }
    if (j.state === 'confirm') toast('Look at the TV: press OK to ' + what);
    else if (j.state === 'installing') toast(j.message || 'Working...');
    else if (j.state === 'done') { toast(j.message || 'Done'); state.store = null; await load('store'); return; }
    else if (['failed', 'denied', 'expired'].includes(j.state)) { toast(j.message || 'Not done'); return; }
  }
}
function storeView() {
  const st = state.store;
  if (!st) return el('main', {}, el('p', {class: 'muted'}, 'Loading...'));
  const out = [];
  out.push(el('div', {class: 'row'},
    el('button', {class: 'btn small', onclick: act(async () => { await api('/store/refresh', {}); state.store = null; await load('store'); })}, 'Refresh'),
    el('button', {class: 'btn small', onclick: () => { state.tab = 'settings'; load('settings'); render(); }}, 'Repositories...')));
  if (!st.repos.length) out.push(el('div', {class: 'card'}, el('b', {}, 'No repositories yet'), el('small', {}, 'Add one under Settings > Apps (one per line: Name | address). Only add repositories you trust: installed apps run with the same rights as the launcher.')));
  for (const r of st.repos) out.push(el('div', {class: 'card'}, el('b', {}, r.name, el('span', {class: 'badge'}, r.ok ? r.count + ' apps' : 'not reachable')),
    el('small', {}, r.ok ? r.url : r.error), ...(r.skipped || []).map(x => el('small', {}, 'skipped ' + x))));
  out.push(el('h2', {}, 'Available'));
  if (!st.available.length) out.push(el('p', {class: 'muted'}, 'Nothing to show.'));
  for (const a of st.available) {
    const label = a.state === 'update' ? 'Update to ' + a.version : a.state === 'new' ? 'Install' : null;
    out.push(el('div', {class: 'card'}, el('b', {}, a.name, el('span', {class: 'badge'}, 'v' + a.version), a.secure ? null : el('span', {class: 'badge'}, 'not encrypted')),
      el('small', {}, [a.description, a.repo, Math.max(1, Math.round(a.size / 1024)) + ' KB'].filter(Boolean).join('  ·  ')),
      a.permissions.length ? el('small', {}, 'Asks for: ' + a.permissions.join('; ')) : null,
      el('div', {class: 'mt8'}, label ? el('button', {class: 'btn small primary', onclick: act(async () => {
        const r = await api('/store/install', {repo: a.repo, id: a.id}); watchJob(r.job, a.state === 'update' ? 'update' : 'install');
      })}, label) : el('span', {class: 'muted'}, a.state === 'installed' ? 'Installed' + (a.installed_version ? ' (v' + a.installed_version + ')' : '') : 'Built in'))));
  }
  out.push(el('h2', {}, 'Installed from repositories'));
  if (!st.installed.length) out.push(el('p', {class: 'muted'}, 'None yet.'));
  for (const a of st.installed) out.push(el('div', {class: 'card'}, el('b', {}, a.name, el('span', {class: 'badge'}, 'v' + a.version)), el('small', {}, a.description),
    el('div', {class: 'mt8'}, el('button', {class: 'btn small danger', onclick: act(async () => { const r = await api('/store/remove', {id: a.id}); watchJob(r.job, 'remove'); })}, 'Remove'))));
  return el('main', {}, ...out);
}

// ---- frame ----
const TABS = [['remote', 'Remote'], ['apps', 'Apps'], ['store', 'Store'], ['settings', 'Settings'], ['system', 'System']];
function render() {
  const s = state.session;
  if (!s) return;
  app.replaceChildren();
  if (!s.paired) { app.append(el('header', {}, el('h1', {}, 'TV Launcher')), pairView()); return; }
  const views = {remote: remoteView, apps: appsView, store: storeView, settings: settingsView, system: systemView};
  app.append(el('header', {}, el('h1', {}, s.server || 'TV Launcher'), el('span', {class: 'muted'}, s.name)),
    el('nav', {}, ...TABS.map(([id, label]) => el('button', {'aria-current': state.tab === id ? 'true' : 'false', onclick: () => { state.tab = id; load(id); render(); }}, label))),
    views[state.tab]());
}
async function load(tab) {
  try {
    if (tab === 'apps' || tab === 'settings') state.apps = (await api('/apps')).apps;
    if (tab === 'settings' && !state.settings) state.settings = await api('/settings');
    if (tab === 'system') { state.devices = (await api('/devices')).devices; state.log = (await api('/log')).lines.join('\n'); }
    if (tab === 'store' && !state.store) state.store = await api('/store');
    if (tab === 'remote' || tab === 'apps') state.status = await api('/status');
  } catch (e) { return; }
  if (state.tab === tab) render();
}
// the poll only refreshes the "now playing" card: rebuilding the whole page every 2 seconds would wipe what you are typing and swallow taps
async function poll() {
  try { state.status = await api('/status'); } catch (e) { return; }
  const n = document.getElementById('now');
  if (n && state.tab === 'remote') n.replaceWith(nowCard());
}
async function boot() {
  try { state.session = await (await fetch('/api/session', {credentials: 'same-origin'})).json(); } catch (e) { app.textContent = 'Cannot reach the TV.'; return; }
  render();
  if (state.session.paired) load(state.tab);
}
boot();
setInterval(() => { if (state.session && state.session.paired && !document.hidden && state.tab === 'remote') poll(); }, 2000);
"""

ASSETS = {
    "/": (HTML.encode(), "text/html; charset=utf-8"),
    "/index.html": (HTML.encode(), "text/html; charset=utf-8"),
    "/app.css": (CSS.encode(), "text/css; charset=utf-8"),
    "/app.js": (JS.encode(), "text/javascript; charset=utf-8"),
}
