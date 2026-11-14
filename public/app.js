/* Jet Tracker 1 — front end. Data: /api/fleet (operators + tails) and /api/state (live + last-known). */
(() => {
const $ = s => document.querySelector(s);
const LS = k => 'jt1.' + k;
let fleet = {operators: [], tails: []}, st = {planes: {}}, opById = {}, map, selected = null, mapReady = false;
const off = new Set(JSON.parse(localStorage.getItem(LS('off')) || '[]'));
$('#sort').value = localStorage.getItem(LS('sort')) || 'status';
$('#showLow').checked = localStorage.getItem(LS('low')) === '1';

const fmtTime = t => {
  const d = new Date(t * 1000), now = new Date();
  const same = d.toDateString() === now.toDateString();
  return same ? d.toLocaleTimeString([], {hour: 'numeric', minute: '2-digit'})
              : d.toLocaleString([], {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'});
};
const ago = s => s < 90 ? 'just now' : s < 3600 ? Math.round(s / 60) + ' min ago' : s < 86400 ? (s / 3600).toFixed(s < 36000 ? 1 : 0) + ' h ago' : Math.round(s / 86400) + ' d ago';
const apName = a => a ? `${a.iata || a.code} · ${a.name}${a.city ? ', ' + a.city : ''}${a.country === 'US' ? ', ' + a.region : a.country ? ', ' + a.country : ''}` : null;
const compass = d => d == null ? '' : ['N','NE','E','SE','S','SW','W','NW'][Math.round(d / 45) % 8];

function status(t) {
  const p = st.planes[t.tail]; const now = st.now || Date.now() / 1000;
  if (!p) return {kind: 'none', rank: 4, label: 'No data yet', line: `Not heard on ADS-B since tracking started${opById[t.op]?.base ? ' · home base ' + opById[t.op].base : ''}`};
  const age = now - p.ts;
  if (!p.onGround && age < 150) {
    const alt = p.altFt != null ? (p.altFt >= 18000 ? 'FL' + Math.round(p.altFt / 100) : p.altFt.toLocaleString() + ' ft') : '—';
    const vs = p.vsFpm > 500 ? ' ↑' : p.vsFpm < -500 ? ' ↓' : '';
    return {kind: 'air', rank: 0, label: 'Airborne', p, age,
      line: `${alt}${vs} · ${p.gsKt != null ? Math.round(p.gsKt) + ' kt' : '—'} · hdg ${p.track != null ? Math.round(p.track) + '° ' + compass(p.track) : '—'}${p.callsign && p.callsign !== t.tail ? ' · ' + p.callsign : ''}`,
      where: p.near ? `near ${apName(p.near)} (${p.near.distNm} nm)` : null};
  }
  if (p.onGround) {
    const ap = p.groundAirport && Math.abs(p.groundAirport.ts - p.ts) < 3600 ? p.groundAirport : p.near;
    return {kind: age < 600 ? 'ground' : 'stale', rank: age < 600 ? 1 : 2, label: age < 600 ? 'On ground' : 'Parked', p, age,
      line: ap ? apName(ap) : `${p.lat.toFixed(3)}, ${p.lon.toFixed(3)}`, where: null};
  }
  // last heard in flight
  const la = p.lastAirport;
  if (la && p.ts - la.ts < 900 && (p.altFt == null || p.altFt < 5000))
    return {kind: 'stale', rank: 2, label: 'Likely landed', p, age, line: apName(la), where: 'signal lost on approach'};
  return {kind: 'stale', rank: 3, label: 'Signal lost', p, age,
    line: `last in flight${p.altFt != null ? ' at ' + p.altFt.toLocaleString() + ' ft' : ''}${p.near ? ' near ' + apName(p.near) : ''}`, where: 'out of receiver coverage, or landed where no receiver hears it'};
}

function visibleTails() {
  const q = $('#q').value.trim().toLowerCase(), low = $('#showLow').checked;
  return fleet.tails.filter(t => !off.has(t.op) && (low || t.confidence !== 'low')).map(t => ({t, s: status(t)}))
    .filter(({t, s}) => !q || [t.tail, t.model, opById[t.op]?.name, s.line, s.where].join(' ').toLowerCase().includes(q));
}

function renderChips() {
  $('#chips').innerHTML = fleet.operators.map(o => {
    const n = fleet.tails.filter(t => t.op === o.id && ($('#showLow').checked || t.confidence !== 'low'));
    const air = n.filter(t => status(t).kind === 'air').length;
    return `<span class="chip ${off.has(o.id) ? 'off' : ''}" data-op="${o.id}" title="${o.certName || ''} · base ${o.base}"><span class="dot" style="background:${o.color}"></span>${o.name} <b>${n.length}</b>${air ? ` <span style="color:#6ee7b7">${air} up</span>` : ''}</span>`;
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
  list.sort((a, b) => sort === 'tail' ? a.t.tail.localeCompare(b.t.tail)
    : sort === 'operator' ? opIdx(a.t.op) - opIdx(b.t.op) || a.s.rank - b.s.rank || a.t.tail.localeCompare(b.t.tail)
    : sort === 'seen' ? (b.s.p?.ts || 0) - (a.s.p?.ts || 0)
    : a.s.rank - b.s.rank || (b.s.p?.ts || 0) - (a.s.p?.ts || 0) || a.t.tail.localeCompare(b.t.tail));
  $('#cards').innerHTML = list.map(({t, s}) => {
    const o = opById[t.op] || {color: '#888', name: t.op};
    const seen = s.p ? `${s.kind === 'air' ? 'Updated' : 'Last seen'} ${fmtTime(s.p.ts)} (${ago(s.age)})` : '';
    return `<div class="card ${selected === t.tail ? 'sel' : ''}" data-tail="${t.tail}" style="border-left-color:${o.color}">
      <div class="top"><span class="tail">${t.tail}<span class="conf ${t.confidence}" title="${(t.source || '').replace(/"/g, '')}">${t.confidence || ''}</span></span><span class="op">${o.name}</span></div>
      <div class="model">${t.model || ''}${t.year ? ' · ' + t.year : ''}</div>
      <div class="status"><span class="badge ${s.kind}">${s.label}</span>${s.line || ''}</div>
      ${s.where ? `<div class="meta">${s.where}</div>` : ''}
      ${seen ? `<div class="meta">${seen}</div>` : ''}
      ${t.note ? `<div class="note">${t.note}</div>` : ''}
    </div>`;
  }).join('') || '<p class="small" style="padding:12px">No tails match.</p>';
}
$('#cards').addEventListener('click', e => {
  const c = e.target.closest('.card'); if (!c) return; select(c.dataset.tail, true);
});
function select(tail, fly) {
  selected = tail; render();
  const p = st.planes[tail];
  if (fly && p && map) { map.flyTo({center: [p.lon, p.lat], zoom: Math.max(map.getZoom(), 7)}); if (document.body.classList.contains('listonly')) document.body.classList.remove('listonly'); }
  if (!p) return;
  document.querySelector(`.card[data-tail="${tail}"]`)?.scrollIntoView({block: 'nearest', behavior: 'smooth'});
}

function geo(list) {
  const pts = [], trails = [];
  for (const {t, s} of list) {
    const p = s.p; if (!p) continue; const o = opById[t.op] || {color: '#888'};
    pts.push({type: 'Feature', geometry: {type: 'Point', coordinates: [p.lon, p.lat]},
      properties: {tail: t.tail, color: o.color, rot: p.track || 0, air: s.kind === 'air' ? 1 : 0, stale: s.kind === 'stale' ? 1 : 0, sel: selected === t.tail ? 1 : 0}});
    if (s.kind === 'air' && p.trail?.length > 1)
      trails.push({type: 'Feature', geometry: {type: 'LineString', coordinates: p.trail.map(x => [x[1], x[0]]).concat([[p.lon, p.lat]])}, properties: {color: o.color}});
  }
  return {pts: {type: 'FeatureCollection', features: pts}, trails: {type: 'FeatureCollection', features: trails}};
}
function renderMap(list) {
  if (!mapReady) return; const g = geo(list);
  map.getSource('planes').setData(g.pts); map.getSource('trails').setData(g.trails);
}

function renderSummary(list) {
  const air = list.filter(x => x.s.kind === 'air').length, gnd = list.filter(x => x.s.kind !== 'air' && x.s.p).length;
  $('#summary').innerHTML = `<b style="color:#6ee7b7">${air}</b> flying · ${gnd} on ground/last seen · ${list.length} tails`;
  const lp = st.lastPoll ? `Live data via ${st.source || '—'} (ADS-B, unfiltered community feed), polled every ${st.pollSeconds}s; last poll ${fmtTime(st.lastPoll)}.` : '';
  $('#foot').innerHTML = `${lp}${st.error ? ' <span style="color:#fbbf24">Feed issue: ' + st.error + '</span>' : ''}<br>Times shown in your local time zone (${Intl.DateTimeFormat().resolvedOptions().timeZone}). Positions come from public ADS-B receivers, so planes parked or flying outside coverage show their last known spot. Fleet list: FAA Part 135 certificate list + FAA registry; edit data/fleet.json to add tails.`;
}

function render() {
  renderChips(); const list = visibleTails(); renderCards(list); renderMap(list); renderSummary(list);
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
  map = new maplibregl.Map({container: 'map', style: 'https://tiles.openfreemap.org/styles/liberty', center: [-88, 34], zoom: 3.4, attributionControl: {compact: true}});
  map.addControl(new maplibregl.NavigationControl({showCompass: false}), 'top-left');
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
  try { st = await (await fetch('api/state', {cache: 'no-store'})).json(); render(); $('#waking').classList.add('hide'); }
  catch (e) { $('#summary').textContent = 'Offline — showing last data'; }
}
async function boot() {
  for (let i = 0; ; i++) { try { fleet = await (await fetch('api/fleet', {cache: 'no-store'})).json(); break; } catch (e) { if (i > 30) throw e; await new Promise(r => setTimeout(r, 3000)); } }
  fleet.operators.forEach(o => opById[o.id] = o);
  initMap(); await refresh(); setInterval(refresh, 15000);
  setInterval(async () => { try { fleet = await (await fetch('api/fleet', {cache: 'no-store'})).json(); opById = {}; fleet.operators.forEach(o => opById[o.id] = o); } catch {} }, 300000);
}
if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js').catch(() => {});
boot();
})();
