/* ParaFlight Jet Tracker 1 — front end.
   Data: /api/fleet (operators + tails), /api/state (live + last-known + last landing/takeoff), /api/events. */
(() => {
const $ = s => document.querySelector(s);
const esc = v => String(v ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
const LS = k => 'jt1.' + k;
let fleet = {operators: [], tails: []}, st = {planes: {}}, opById = {}, map, selected = null, mapReady = false, popup = null;
let recentLanded = [], fetchedAt = 0, fetchFailed = false, clockSkew = 0;
const off = new Set(JSON.parse(localStorage.getItem(LS('off')) || '[]'));
$('#sort').value = localStorage.getItem(LS('sort')) || 'status';
$('#showLow').checked = localStorage.getItem(LS('low')) === '1';
const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;
const nowS = () => Date.now() / 1000 + clockSkew;   // server-aligned "now"

// ---------- formatting (viewer's local time, with zone label)
const fmtTime = (t, withZone = true) => {
  if (!t) return '—';
  const d = new Date(t * 1000), now = new Date();
  const o = d.toDateString() === now.toDateString() ? {hour: 'numeric', minute: '2-digit'} : {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'};
  if (withZone) o.timeZoneName = 'short';
  return d.toLocaleString([], o);
};
const fmtDay = t => { const d = new Date(t * 1000); return d.toDateString() === new Date().toDateString() ? 'today ' + d.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'}) : d.toLocaleDateString([], {month: 'short', day: 'numeric'}); };
const ago = s => s < 0 ? 'just now' : s < 90 ? 'just now' : s < 3600 ? Math.round(s / 60) + ' min ago' : s < 86400 ? (s / 3600).toFixed(s < 36000 ? 1 : 0) + ' h ago' : Math.round(s / 86400) + ' d ago';
const dur = s => s == null ? '—' : s < 3600 ? Math.max(1, Math.round(s / 60)) + 'm' : Math.floor(s / 3600) + 'h ' + String(Math.round((s % 3600) / 60)).padStart(2, '0') + 'm';
const apCode = a => a ? (a.iata || a.code) : null;
const apName = a => a ? `${a.iata || a.code} · ${a.name}${a.city ? ', ' + a.city : ''}${a.country === 'US' ? ', ' + a.region : a.country ? ', ' + a.country : ''}` : null;
const compass = d => d == null ? '' : ['N','NE','E','SE','S','SW','W','NW'][Math.round(d / 45) % 8];
const altTxt = a => a == null ? '—' : a >= 18000 ? 'FL' + Math.round(a / 100) : a.toLocaleString() + ' ft';
const HOW = {'ground-flag': 'seen on ADS-B', 'signal-loss': 'inferred: signal lost low near the airport', 'reappeared-on-ground': 'inferred: next heard on the ground here',
  'moved-while-unheard': 'inferred: found parked here after an unheard flight', 'first-seen-airborne': 'inferred: first heard already airborne', 'first-seen-climbing': 'inferred: first heard climbing out'};

// ---------- per-tail status
function status(t) {
  const p = st.planes[t.tail]; const now = nowS();
  if (!p) return {kind: 'none', rank: 4, label: 'Never seen yet', line: `Not heard on ADS-B since tracking started${opById[t.op]?.base ? ' · home base ' + opById[t.op].base : ''}`};
  const age = now - p.ts;
  const L = p.lastLanding, T = p.lastTakeoff;
  const flying = T && (!L || T.ts > L.ts) ? T : null;
  const base = {p, age, landed: L, origin: flying};
  if (!p.onGround && age < 150) {
    const vs = p.vsFpm > 500 ? ' ↑' : p.vsFpm < -500 ? ' ↓' : '';
    return {...base, kind: 'air', rank: 0, label: 'Airborne',
      line: `${altTxt(p.altFt)}${vs} · ${p.gsKt != null ? Math.round(p.gsKt) + ' kt' : '—'} · hdg ${p.track != null ? Math.round(p.track) + '° ' + compass(p.track) : '—'}${p.callsign && p.callsign !== t.tail ? ' · ' + p.callsign : ''}`,
      where: p.near ? `near ${apName(p.near)} (${p.near.distNm} nm)` : null};
  }
  if (p.onGround || p.phase === 'ground') {
    const ap = p.onGround ? (p.groundAirport && Math.abs(p.groundAirport.ts - p.ts) < 3600 ? p.groundAirport : p.near) : (p.groundAirport || L);
    const fresh = p.onGround && age < 600;
    return {...base, kind: fresh ? 'ground' : 'stale', rank: fresh ? 1 : 2, label: fresh ? 'On ground' : p.onGround ? 'Parked' : 'Landed', at: ap,
      line: ap ? apName(ap) : `${p.lat.toFixed(3)}, ${p.lon.toFixed(3)}`, where: !p.onGround && L ? HOW[L.how] : null};
  }
  return {...base, kind: 'stale', rank: 3, label: 'Signal lost', at: p.near,
    line: `last in flight${p.altFt != null ? ' at ' + p.altFt.toLocaleString() + ' ft' : ''}${p.near ? ' near ' + apName(p.near) : ''}`, where: 'out of receiver coverage, or landed where no receiver hears it'};
}
const landedTxt = s => s.landed ? `Landed ${apCode(s.landed) || '?'} ${ago(nowS() - s.landed.ts)}${s.landed.inferred ? ' (inferred)' : ''}` : '';
const seenTxt = s => s.p ? `${apCode(s.at || s.p.groundAirport || s.p.near) || 'Last seen'} · ${fmtDay(s.p.ts)}` : 'never seen yet';
const statBucket = s => {
  if (s.kind === 'air') return 'air'; if (!s.p) return 'never';
  const a = nowS() - s.p.ts, midnight = new Date(); midnight.setHours(0, 0, 0, 0);
  if (s.p.ts * 1000 >= midnight.getTime() || a < 6 * 3600) return 'today';
  return a < 7 * 86400 ? 'week' : 'old';
};

// ---------- data badge
function renderFresh() {
  const el = $('#fresh'); const ls = st.lastSuccess;
  if (!ls) { el.className = 'fresh red'; el.textContent = fetchFailed ? 'Offline' : 'No data yet'; return; }
  const age = Math.max(0, nowS() - ls);
  el.className = 'fresh ' + (fetchFailed || age > 300 ? 'red' : age > 90 ? 'amber' : 'green');
  const t = age < 120 ? Math.round(age) + ' sec' : age < 7200 ? Math.round(age / 60) + ' min' : Math.round(age / 3600) + ' h';
  el.textContent = `Data ${t} old · ${st.source || '—'}${fetchFailed ? ' · offline' : ''}`;
  el.title = `Newest ADS-B poll that succeeded: ${fmtTime(ls)}${st.error ? '\nFeed issue: ' + st.error : ''}`;
}
setInterval(renderFresh, 1000);

// ---------- list of visible tails (map view filter: operator chips + search + low-confidence toggle)
function visibleTails() {
  const q = $('#q').value.trim().toLowerCase(), low = $('#showLow').checked;
  return fleet.tails.filter(t => !off.has(t.op) && (low || t.confidence !== 'low')).map(t => ({t, s: status(t)}))
    .filter(({t, s}) => !q || [t.tail, t.model, opById[t.op]?.name, s.line, s.where, apCode(s.landed)].join(' ').toLowerCase().includes(q));
}
const allTails = () => fleet.tails.map(t => ({t, s: status(t)}));

function renderChips() {
  $('#chips').innerHTML = fleet.operators.map(o => {
    const n = fleet.tails.filter(t => t.op === o.id && ($('#showLow').checked || t.confidence !== 'low'));
    const air = n.filter(t => status(t).kind === 'air').length;
    return `<span class="chip ${off.has(o.id) ? 'off' : ''}" data-op="${esc(o.id)}" title="${esc(o.certName || '')} · base ${esc(o.base)}"><span class="dot" style="background:${esc(o.color)}"></span><span class="${air ? 'opfly' : ''}">${esc(o.name)}</span> <b>${n.length}</b>${air ? ` <span style="color:#6ee7b7">${air} up</span>` : ''}</span>`;
  }).join('') + `<span class="chip" data-op="__all">All</span><span class="chip" data-op="__none">None</span>`;
}
$('#chips').addEventListener('click', e => {
  const c = e.target.closest('.chip'); if (!c) return; const id = c.dataset.op;
  if (id === '__all') off.clear(); else if (id === '__none') fleet.operators.forEach(o => off.add(o.id));
  else off.has(id) ? off.delete(id) : off.add(id);
  localStorage.setItem(LS('off'), JSON.stringify([...off])); render();
});

function renderCards(list) {
  const sort = $('#sort').value;
  const opIdx = id => fleet.operators.findIndex(o => o.id === id);
  list = [...list].sort((a, b) => sort === 'tail' ? a.t.tail.localeCompare(b.t.tail)
    : sort === 'operator' ? opIdx(a.t.op) - opIdx(b.t.op) || a.s.rank - b.s.rank || a.t.tail.localeCompare(b.t.tail)
    : sort === 'seen' ? (b.s.p?.ts || 0) - (a.s.p?.ts || 0)
    : sort === 'landed' ? (b.s.landed?.ts || 0) - (a.s.landed?.ts || 0)
    : a.s.rank - b.s.rank || (b.s.p?.ts || 0) - (a.s.p?.ts || 0) || a.t.tail.localeCompare(b.t.tail));
  $('#cards').innerHTML = list.map(({t, s}) => {
    const o = opById[t.op] || {color: '#888', name: t.op};
    const seen = s.p ? `${s.kind === 'air' ? 'Updated' : 'Last seen'} ${fmtTime(s.p.ts)} (${ago(s.age)})` : '';
    const up = s.kind === 'air' && s.origin ? `From ${apCode(s.origin) || '?'} · up ${s.origin.inferred ? '~' : ''}${dur(nowS() - s.origin.ts)}` : '';
    return `<div class="card ${selected === t.tail ? 'sel' : ''}" data-tail="${esc(t.tail)}" style="border-left-color:${esc(o.color)}">
      <div class="top"><span class="tail">${esc(t.tail)}<span class="conf ${esc(t.confidence)}" title="${esc(t.source)}">${esc(t.confidence || '')}</span></span><span class="op">${esc(o.name)}</span></div>
      <div class="model">${esc(t.model || '')}${t.year ? ' · ' + esc(t.year) : ''}</div>
      <div class="status"><span class="badge ${s.kind}">${s.label}</span>${esc(s.line || '')}</div>
      ${s.where ? `<div class="meta">${esc(s.where)}</div>` : ''}
      ${up ? `<div class="meta">${esc(up)}</div>` : ''}
      ${s.landed && s.kind !== 'air' || s.landed && !up ? `<div class="meta landed" title="${esc(fmtTime(s.landed.ts) + ' · ' + (HOW[s.landed.how] || ''))}">🛬 ${esc(landedTxt(s))}</div>` : ''}
      ${seen ? `<div class="meta">${esc(seen)}</div>` : ''}
      ${t.note ? `<div class="note">${esc(t.note)}</div>` : ''}
    </div>`;
  }).join('') || '<p class="small" style="padding:12px">No tails match.</p>';
}
$('#cards').addEventListener('click', e => { const c = e.target.closest('.card'); if (c) select(c.dataset.tail, true); });

// ---------- sortable tables
const sorts = {active: JSON.parse(localStorage.getItem(LS('sortA')) || '{"k":"up","d":1}'), fleet: JSON.parse(localStorage.getItem(LS('sortF')) || '{"k":"status","d":1}')};
function sortRows(rows, cfg, getters) {
  const g = getters[cfg.k] || getters.tail;
  return rows.sort((a, b) => {
    const x = g(a), y = g(b);
    if (x == null && y == null) return a.t.tail.localeCompare(b.t.tail);
    if (x == null) return 1; if (y == null) return -1;   // blanks always last
    const r = typeof x === 'string' ? x.localeCompare(y) : x - y;
    return (r || a.t.tail.localeCompare(b.t.tail)) * (cfg.d === -1 ? -1 : 1) * (typeof x === 'string' ? 1 : 1);
  });
}
function markHeaders(tbl, cfg) {
  tbl.querySelectorAll('th').forEach(th => { th.classList.toggle('sorted', th.dataset.k === cfg.k); th.dataset.dir = th.dataset.k === cfg.k ? (cfg.d === -1 ? '▼' : '▲') : ''; });
}
function hookSort(tbl, name, key) {
  tbl.querySelector('thead').addEventListener('click', e => {
    const th = e.target.closest('th'); if (!th) return; const c = sorts[name];
    if (c.k === th.dataset.k) c.d = -c.d; else { c.k = th.dataset.k; c.d = ['tail', 'op', 'type', 'from', 'status'].includes(c.k) ? 1 : -1; }
    localStorage.setItem(LS(key), JSON.stringify(c)); render();
  });
}
hookSort($('#activeTbl'), 'active', 'sortA'); hookSort($('#fleetTbl'), 'fleet', 'sortF');
const opName = t => opById[t.op]?.name || t.op;
const LG_RED = '#ef4444';
const opLg = (t, lg, why) => lg ? `<span class="lg" title="Lifeguard / medevac${why ? ' · ' + esc(why) : ''}">${esc(opName(t))} ✚</span>` : esc(opName(t));
const opDot = t => `<span class="dot" style="background:${esc(opById[t.op]?.color || '#888')}"></span>`;
const typeOf = t => t.model || st.planes[t.tail]?.type || '';

function renderActive() {
  const rows = allTails().filter(x => x.s.kind === 'air' && !off.has(x.t.op));
  $('#nActive').textContent = rows.length;
  const G = {tail: r => r.t.tail, op: r => opName(r.t), type: r => typeOf(r.t), alt: r => r.s.p.altFt, gs: r => r.s.p.gsKt, hdg: r => r.s.p.track,
    from: r => apCode(r.s.origin), up: r => r.s.origin ? nowS() - r.s.origin.ts : null};
  sortRows(rows, sorts.active, G); markHeaders($('#activeTbl'), sorts.active);
  $('#activeTbl tbody').innerHTML = rows.map(({t, s}) => {
    const p = s.p, vs = p.vsFpm > 500 ? '↑' : p.vsFpm < -500 ? '↓' : '';
    return `<tr data-tail="${esc(t.tail)}" class="${selected === t.tail ? 'sel' : ''}">
      <td class="b">${opDot(t)}${esc(t.tail)}</td><td>${opLg(t, p.lifeguard, p.lifeguardReason)}</td><td class="hm">${esc(typeOf(t))}</td>
      <td class="num">${p.altFt == null ? '—' : p.altFt >= 18000 ? 'FL' + Math.round(p.altFt / 100) : p.altFt.toLocaleString()}${vs}</td><td class="num">${p.gsKt != null ? Math.round(p.gsKt) : '—'}</td>
      <td class="num">${p.track != null ? Math.round(p.track) + '°' : '—'}</td>
      <td title="${esc(s.origin ? apName(s.origin) + ' · ' + (HOW[s.origin.how] || '') : 'Takeoff not observed')}">${esc(apCode(s.origin) || '—')}</td>
      <td class="num" title="${s.origin ? 'Took off ' + esc(fmtTime(s.origin.ts)) : ''}">${s.origin ? (s.origin.inferred ? '~' : '') + dur(nowS() - s.origin.ts) : '—'}</td></tr>`;
  }).join('');
  $('#activeEmpty').textContent = rows.length ? 'Tap a row to fly the map to it. "~" = takeoff time inferred (first heard already airborne). Red operator = has a plane flying; ✚ = Lifeguard/medevac.' : 'No tracked tails are airborne right now.';
}
$('#activeTbl tbody').addEventListener('click', e => { const r = e.target.closest('tr'); if (r) select(r.dataset.tail, true); });

function renderLanded() {
  const tb = $('#landedTbl tbody'); if (!tb) return;
  const byTail = Object.fromEntries(fleet.tails.map(t => [t.tail, t]));
  tb.innerHTML = recentLanded.map(e => {
    const t = byTail[e.tail] || {tail: e.tail, op: ''}, a = e.ap, o = e.origin;
    return `<tr data-tail="${esc(e.tail)}" class="${selected === e.tail ? 'sel' : ''}">
      <td class="b">${opDot(t)}${esc(e.tail)}<span class="sub mob">${opLg(t, e.lifeguard, e.lifeguardReason)} · ${esc(typeOf(t))}</span></td>
      <td class="hm2">${opLg(t, e.lifeguard, e.lifeguardReason)}</td><td class="hm">${esc(typeOf(t))}</td>
      <td title="${esc(apName(a) || '')}"><b>${esc(apCode(a) || '?')}</b>${a?.city ? `<span class="sub">${esc(a.city)}${a.region && a.country === 'US' ? ', ' + esc(a.region) : ''}</span>` : ''}</td>
      <td>${esc(fmtTime(e.ts))}<span class="sub">${ago(nowS() - e.ts)}${e.inferred ? ` · <span class="inf" title="${esc(HOW[e.how] || 'inferred')}">inferred</span>` : ''}</span></td>
      <td title="${o ? esc((apName(o.ap) || '') + ' · took off ' + fmtTime(o.ts)) : 'Takeoff not observed'}">${o && o.ap ? `<b>${esc(apCode(o.ap))}</b><span class="sub">${o.inferred ? '~' : ''}${esc(fmtTime(o.ts, false))}</span>` : '<span class="muted">—</span>'}</td></tr>`;
  }).join('') || '<tr><td colspan="6" class="muted">No landings recorded yet.</td></tr>';
  $('#landedNote').textContent = recentLanded.length ? `Newest first, whole fleet. Times in your local zone (${TZ}). "Inferred" = signal lost low near that airport. Tap a row to see the plane. ✚ = Lifeguard/medevac flight.` : '';
}
$('#landedTbl tbody').addEventListener('click', e => { const r = e.target.closest('tr[data-tail]'); if (r) select(r.dataset.tail, true); });

function fillFilters() {
  const fop = $('#fop'), ft = $('#ftype'), cur = [fop.value, ft.value];
  fop.innerHTML = '<option value="">All operators</option>' + fleet.operators.map(o => `<option value="${esc(o.id)}">${esc(o.name)}</option>`).join('');
  const types = [...new Set(fleet.tails.map(typeOf).filter(Boolean))].sort();
  ft.innerHTML = '<option value="">All types</option>' + types.map(x => `<option>${esc(x)}</option>`).join('');
  fop.value = cur[0]; ft.value = cur[1];
}
function renderFleetTable() {
  if (!document.body.classList.contains('view-table')) return;
  const q = $('#tq').value.trim().toLowerCase(), fo = $('#fop').value, fty = $('#ftype').value, fs = $('#fstat').value;
  let rows = allTails().filter(({t, s}) => (!fo || t.op === fo) && (!fty || typeOf(t) === fty) && (!fs || statBucket(s) === fs)
    && (!q || [t.tail, opName(t), typeOf(t), s.label, s.line, apCode(s.landed), apCode(s.at)].join(' ').toLowerCase().includes(q)));
  const G = {tail: r => r.t.tail, op: r => opName(r.t), type: r => typeOf(r.t), status: r => r.s.rank * 1e10 - (r.s.p?.ts || 0),
    landed: r => r.s.landed?.ts ?? null, seen: r => r.s.p?.ts ?? null};
  sortRows(rows, sorts.fleet, G); markHeaders($('#fleetTbl'), sorts.fleet);
  $('#tcount').textContent = `${rows.length} of ${fleet.tails.length} tails`;
  $('#fleetTbl tbody').innerHTML = rows.map(({t, s}) => {
    const L = s.landed;
    return `<tr data-tail="${esc(t.tail)}">
      <td class="b">${opDot(t)}${esc(t.tail)}<span class="sub mob">${esc(opName(t))} · ${esc(typeOf(t))}</span></td><td class="hm2">${esc(opName(t))}</td><td class="hm2">${esc(typeOf(t))}</td>
      <td><span class="badge ${s.kind}">${s.label}</span>${s.kind === 'air' ? `<span class="sub">${altTxt(s.p.altFt)} · ${s.p.gsKt != null ? Math.round(s.p.gsKt) + ' kt' : ''}${s.origin ? ' · from ' + esc(apCode(s.origin)) : ''}</span>` : ''}</td>
      <td title="${L ? esc(apName(L) + ' · ' + (HOW[L.how] || '')) : ''}">${L ? `<b>${esc(apCode(L) || '?')}</b> <span class="sub">${esc(fmtTime(L.ts))} · ${ago(nowS() - L.ts)}${L.inferred ? ' · inferred' : ''}</span>` : '<span class="muted">—</span>'}</td>
      <td>${s.p ? `${esc(apCode(s.at || s.p.near) || '')} <span class="sub">${esc(fmtTime(s.p.ts))} · ${ago(s.age)}</span>` : '<span class="muted">never seen yet</span>'}</td></tr>`;
  }).join('') || '<tr><td colspan="6" class="muted">No tails match.</td></tr>';
  $('#tzNote').textContent = `Times are in your local time zone (${TZ}). "Inferred" landings: the signal was lost low near that airport (common below receiver coverage). Tap a row to see it on the map.`;
}
['input', 'change'].forEach(ev => ['#tq', '#fop', '#ftype', '#fstat'].forEach(id => $(id).addEventListener(ev, renderFleetTable)));
$('#fleetTbl tbody').addEventListener('click', e => { const r = e.target.closest('tr[data-tail]'); if (!r) return; setView('map'); select(r.dataset.tail, true); });

// ---------- views & tabs
function setView(v) {
  document.body.classList.toggle('view-map', v === 'map'); document.body.classList.toggle('view-table', v === 'table');
  document.querySelectorAll('#viewseg button').forEach(b => b.classList.toggle('on', b.dataset.view === v));
  localStorage.setItem(LS('view'), v); if (v === 'map') setTimeout(() => map?.resize(), 50); render();
}
$('#viewseg').addEventListener('click', e => { const b = e.target.closest('button'); if (b) setView(b.dataset.view); });
function setTab(tab) {
  document.querySelectorAll('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
  $('#tab-active').classList.toggle('hide', tab !== 'active'); $('#tab-cards').classList.toggle('hide', tab !== 'cards');
  localStorage.setItem(LS('tab'), tab);
}
$('#tabs').addEventListener('click', e => { const b = e.target.closest('button'); if (b) setTab(b.dataset.tab); });

// ---------- selection + popup
function popupHtml(tail) {
  const t = fleet.tails.find(x => x.tail === tail); if (!t) return '';
  const s = status(t), o = opById[t.op] || {}, p = s.p;
  const rows = [];
  rows.push(`<div class="pp-h"><b>${esc(t.tail)}</b> ${p?.lifeguard ? opLg(t, true, p.lifeguardReason) : `<span class="muted">${esc(o.name || '')}</span>`}</div><div class="muted">${esc(typeOf(t))}${t.year ? ' · ' + esc(t.year) : ''}</div>`);
  rows.push(`<div><span class="badge ${s.kind}">${s.label}</span>${esc(s.line || '')}</div>`);
  if (p?.lifeguard) rows.push(`<div class="lg">✚ Lifeguard / medevac <span class="muted">(${esc(p.lifeguardReason || '')})</span></div>`);
  if (s.kind === 'air' && s.origin) rows.push(`<div>🛫 From <b>${esc(apCode(s.origin))}</b> · ${esc(fmtTime(s.origin.ts))} · up ${s.origin.inferred ? '~' : ''}${dur(nowS() - s.origin.ts)}</div>`);
  if (s.landed) rows.push(`<div>🛬 Landed <b>${esc(apCode(s.landed) || '?')}</b> · ${esc(fmtTime(s.landed.ts))} (${ago(nowS() - s.landed.ts)})${s.landed.inferred ? `<br><span class="muted">${esc(HOW[s.landed.how] || 'inferred')}</span>` : ''}</div>`);
  else rows.push(`<div class="muted">🛬 No landing recorded yet</div>`);
  if (p) rows.push(`<div class="muted">Last seen ${esc(fmtTime(p.ts))} (${ago(s.age)})</div>`);
  return `<div class="pp">${rows.join('')}</div>`;
}
function openPopup(tail) {
  const p = st.planes[tail]; if (!p || !mapReady) return;
  popup?.remove();
  popup = new maplibregl.Popup({closeButton: true, maxWidth: '290px', offset: 12}).setLngLat([p.lon, p.lat]).setHTML(popupHtml(tail)).addTo(map);
  popup.on('close', () => { if (popup && popup._jt === tail) popup = null; }); popup._jt = tail;
}
function select(tail, fly) {
  selected = tail; render();
  const p = st.planes[tail];
  if (fly && p && map) {
    if (document.body.classList.contains('listonly')) { document.body.classList.remove('listonly'); $('#toggle').textContent = 'List'; map.resize(); }
    map.flyTo({center: [p.lon, p.lat], zoom: Math.max(map.getZoom(), 7)});
  }
  if (p) openPopup(tail);
  document.querySelector(`.card[data-tail="${tail}"]`)?.scrollIntoView({block: 'nearest', behavior: 'smooth'});
}

// ---------- map
function geo(list) {
  const pts = [], trails = [];
  for (const {t, s} of list) {
    const p = s.p; if (!p) continue; const o = opById[t.op] || {color: '#888'};
    pts.push({type: 'Feature', geometry: {type: 'Point', coordinates: [p.lon, p.lat]},
      properties: {tail: t.tail, color: p.lifeguard ? LG_RED : o.color, lg: p.lifeguard ? 1 : 0, rot: p.track || 0, air: s.kind === 'air' ? 1 : 0, stale: s.kind === 'stale' ? 1 : 0, sel: selected === t.tail ? 1 : 0}});
    if (s.kind === 'air' && p.trail?.length > 1)
      trails.push({type: 'Feature', geometry: {type: 'LineString', coordinates: p.trail.map(x => [x[1], x[0]]).concat([[p.lon, p.lat]])}, properties: {color: p.lifeguard ? LG_RED : o.color}});
  }
  return {pts: {type: 'FeatureCollection', features: pts}, trails: {type: 'FeatureCollection', features: trails}};
}
function renderMap(list) {
  if (!mapReady) return; const g = geo(list);
  map.getSource('planes').setData(g.pts); map.getSource('trails').setData(g.trails);
  if (popup && popup._jt && st.planes[popup._jt]) { const p = st.planes[popup._jt]; popup.setLngLat([p.lon, p.lat]).setHTML(popupHtml(popup._jt)); }
}

function renderSummary(list) {
  const air = list.filter(x => x.s.kind === 'air').length, gnd = list.filter(x => x.s.kind !== 'air' && x.s.p).length, never = list.filter(x => !x.s.p).length;
  $('#summary').innerHTML = `<b style="color:#6ee7b7">${air}</b> flying · ${gnd} on ground / last seen · ${never} never seen yet · ${list.length} tails`;
  const h = st.health || {};
  $('#foot').innerHTML = `${st.lastPoll ? `Live data via ${esc(st.source || '—')} (ADS-B, unfiltered community feeds; automatic failover adsb.lol ⇄ adsb.fi), polled every ${st.pollSeconds}s; last poll ${esc(fmtTime(st.lastPoll))}.` : ''}${st.error ? ' <span style="color:#fbbf24">Feed issue: ' + esc(st.error) + '</span>' : ''}
    ${h.storage ? `<br>History storage: ${Object.entries(h.storage).map(([k, v]) => `${k} ${v.ok ? '✓' : '✗'}`).join(' · ')} · ${h.events ?? 0} takeoff/landing events recorded.` : ''}
    <br>Times shown in your local time zone (${esc(TZ)}). Landings are detected from ADS-B (on-ground flag, or signal lost low near an airport = "inferred"). Planes parked or flying outside coverage show their last known spot.`;
}

function render() {
  const list = visibleTails();
  $('#nAll').textContent = list.length;
  if (document.body.classList.contains('view-map')) { renderChips(); renderCards(list); renderMap(list); renderActive(); renderLanded(); }
  renderSummary(list); renderFleetTable(); renderFresh();
}
['input', 'change'].forEach(ev => { $('#q').addEventListener(ev, render); });
$('#sort').addEventListener('change', () => { localStorage.setItem(LS('sort'), $('#sort').value); render(); });
$('#showLow').addEventListener('change', () => { localStorage.setItem(LS('low'), $('#showLow').checked ? '1' : '0'); render(); });
$('#toggle').addEventListener('click', () => { document.body.classList.toggle('listonly'); $('#toggle').textContent = document.body.classList.contains('listonly') ? 'Map' : 'List'; map?.resize(); });

function planeIcon() {
  const s = 64, c = document.createElement('canvas'); c.width = c.height = s; const x = c.getContext('2d');
  x.fillStyle = '#fff'; x.beginPath();
  const P = [[32,4],[36,10],[36,26],[60,40],[60,45],[36,38],[35,52],[44,58],[44,62],[32,59],[20,62],[20,58],[29,52],[28,38],[4,45],[4,40],[28,26],[28,10]];
  P.forEach(([a, b], i) => i ? x.lineTo(a, b) : x.moveTo(a, b)); x.closePath(); x.fill();
  return x.getImageData(0, 0, s, s);
}
function initMap() {
  if (typeof maplibregl === 'undefined') { $('#map').innerHTML = '<p class="small pad">Map failed to load — the table and lists still work.</p>'; return; }
  map = new maplibregl.Map({container: 'map', style: 'https://tiles.openfreemap.org/styles/liberty', center: [-88, 34], zoom: 3.4, attributionControl: {compact: true}});
  map.addControl(new maplibregl.NavigationControl({showCompass: false}), 'top-left');
  const lgd = document.createElement('div'); lgd.className = 'maplegend'; lgd.title = 'Red operator name = at least one of its planes is airborne now. ✚ (and a red plane icon) = Lifeguard/medevac: transponder lifeguard status or a lifeguard callsign marker';
  lgd.innerHTML = '<span style="color:#ef4444;font-weight:800">Red operator</span> = has a plane flying; <span style="color:#ef4444;font-weight:800">✚</span> = Lifeguard/medevac'; $('#map').appendChild(lgd);
  map.on('load', () => {
    map.addImage('plane', planeIcon(), {sdf: true});
    map.addSource('planes', {type: 'geojson', data: {type: 'FeatureCollection', features: []}});
    map.addSource('trails', {type: 'geojson', data: {type: 'FeatureCollection', features: []}});
    map.addLayer({id: 'trails', type: 'line', source: 'trails', paint: {'line-color': ['get', 'color'], 'line-width': 2, 'line-opacity': .7}});
    map.addLayer({id: 'ground', type: 'circle', source: 'planes', filter: ['==', ['get', 'air'], 0],
      paint: {'circle-radius': ['case', ['==', ['get', 'sel'], 1], 9, 6], 'circle-color': ['get', 'color'], 'circle-opacity': ['case', ['==', ['get', 'stale'], 1], .6, 1], 'circle-stroke-color': '#fff', 'circle-stroke-width': ['case', ['==', ['get', 'sel'], 1], 3, 1.5]}});
    map.addLayer({id: 'air', type: 'symbol', source: 'planes', filter: ['==', ['get', 'air'], 1],
      layout: {'icon-image': 'plane', 'icon-size': ['case', ['==', ['get', 'sel'], 1], .65, .45], 'icon-rotate': ['get', 'rot'], 'icon-rotation-alignment': 'map', 'icon-allow-overlap': true},
      paint: {'icon-color': ['get', 'color'], 'icon-halo-color': '#fff', 'icon-halo-width': 1.5}});
    map.addLayer({id: 'labels', type: 'symbol', source: 'planes',
      layout: {'text-field': ['get', 'tail'], 'text-size': 11, 'text-offset': [0, 1.3], 'text-anchor': 'top', 'text-allow-overlap': false, 'text-font': ['Noto Sans Bold']},
      paint: {'text-color': '#111', 'text-halo-color': '#fff', 'text-halo-width': 1.5}});
    for (const l of ['ground', 'air']) {
      map.on('click', l, e => select(e.features[0].properties.tail, false));
      map.on('mouseenter', l, () => map.getCanvas().style.cursor = 'pointer');
      map.on('mouseleave', l, () => map.getCanvas().style.cursor = '');
    }
    mapReady = true; render();
  });
}

async function refresh() {
  try {
    const r = await fetch('api/state', {cache: 'no-store'}); if (!r.ok) throw new Error(r.status);
    try { const rl = await fetch('api/events?recent=24', {cache: 'no-store'}); if (rl.ok) recentLanded = (await rl.json()).events || []; } catch {}
    st = await r.json(); fetchedAt = Date.now(); fetchFailed = false; clockSkew = st.now - Date.now() / 1000;
    render(); $('#waking').classList.add('hide');
  } catch (e) { fetchFailed = true; renderFresh(); $('#summary').textContent = 'Offline — showing last data'; }
}
async function loadFleet() {
  const f = await (await fetch('api/fleet', {cache: 'no-store'})).json();
  f.operators = [...f.operators].sort((x, y) => (x.name || x.id).localeCompare(y.name || y.id, undefined, {sensitivity: 'base'}));  // A–Z everywhere
  fleet = f; opById = {}; fleet.operators.forEach(o => opById[o.id] = o); fillFilters();
}
async function boot() {
  for (let i = 0; ; i++) { try { await loadFleet(); break; } catch (e) { if (i > 30) throw e; await new Promise(r => setTimeout(r, 3000)); } }
  setTab(localStorage.getItem(LS('tab')) || 'active');
  const v = new URLSearchParams(location.search).get('view') || localStorage.getItem(LS('view')) || 'map';
  initMap(); setView(v === 'table' ? 'table' : 'map');
  await refresh(); setInterval(refresh, 15000);
  setInterval(() => loadFleet().then(render).catch(() => {}), 300000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
}
if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
boot();
})();
