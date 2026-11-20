#!/usr/bin/env python3
"""ParaFlight Jet Tracker 1 - backend.
Polls free public ADS-B APIs (adsb.lol, fallback opendata.adsb.fi) for the tails in data/fleet.json,
detects takeoffs/landings, keeps last-seen state + event history (Postgres / Key Value / file, see store.py),
and serves the static app in public/.  Run: python3 server.py [port]"""
import json, math, os, re, sys, threading, time, urllib.request, urllib.parse, gzip, traceback
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from store import Store

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'data'); PUB = os.path.join(ROOT, 'public')
FLEET = os.path.join(DATA, 'fleet.json')
SEED_STATE = os.path.join(DATA, 'state.json')            # committed seed of last-known positions (read-only)
RUNTIME = os.path.join(DATA, 'runtime', 'history.json')   # local working copy (gitignored)
POLL_S = int(os.environ.get('POLL_SECONDS', '30'))
UA = 'ParaFlightJetTracker1/2.0 (small private fleet tracker, ~1 req/30s)'
SOURCES = [('adsb.lol', 'https://api.adsb.lol/v2/hex/{}'),
           ('adsb.fi', 'https://opendata.adsb.fi/api/v2/hex/{}')]
TRAIL_MAX = 400
LOSS_S = 600          # airborne plane unheard this long -> decide landed (low, near airport) or lost at altitude
RETRACT_S = 1500      # an inferred landing is withdrawn if the plane reappears airborne within this many seconds
EVENTS_MEM = 20000

def log(*a): print(*a, flush=True)

lock = threading.Lock()
fleet = {'operators': [], 'tails': []}; fleet_mtime = 0
state = {'planes': {}, 'lastPoll': None, 'lastSuccess': None, 'lastSource': None, 'lastError': None,
         'started': time.time(), 'polls': 0, 'pollErrors': 0}
events = []           # chronological list of event dicts
event_keys = set()
src_health = {n: {'fails': 0, 'retryAt': 0, 'lastOk': None, 'lastError': None} for n, _ in SOURCES}

# ---------------------------------------------------------------- airports (OurAirports subset, grid index)
AP = json.load(open(os.path.join(DATA, 'airports.json')))
GRID = {}; AP_BY_CODE = {_a[0]: _a for _a in AP}
for _a in AP: GRID.setdefault((int(math.floor(_a[6])), int(math.floor(_a[7]))), []).append(_a)
def nm(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = math.sin((lat2-lat1)*p/2)**2 + math.cos(lat1*p)*math.cos(lat2*p)*math.sin((lon2-lon1)*p/2)**2
    return 2 * 3440.065 * math.asin(math.sqrt(min(1, a)))
def nearest_airport(lat, lon, maxnm=40, minsize=1):
    if lat is None or lon is None: return None
    best = None; r = 1 if maxnm <= 50 else 2
    cy, cx = int(math.floor(lat)), int(math.floor(lon))
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            for a in GRID.get((cy + dy, cx + dx), ()):
                if a[8] < minsize: continue
                d = nm(lat, lon, a[6], a[7])
                score = d - (1.5 if a[8] >= 2 else 0)  # prefer real airports over tiny strips when close
                if d <= maxnm and (best is None or score < best[0]): best = (score, d, a)
    if not best: return None
    a = best[2]
    return {'code': a[0], 'iata': a[1], 'name': a[2], 'city': a[3], 'region': a[4], 'country': a[5], 'distNm': round(best[1], 1), 'lat': a[6], 'lon': a[7]}

# ---------------------------------------------------------------- fleet
def faa_hex(tail):
    n = tail.upper().lstrip('N')
    url = 'https://registry.faa.gov/AircraftInquiry/Search/NNumberResult?nNumberTxt=' + n
    html = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20).read().decode('utf-8', 'replace')
    m = re.search(r'Mode S Code \(Base 16 / Hex\)</td>\s*<td[^>]*>\s*([0-9A-Fa-f]{6})', html)
    return m.group(1).lower() if m else None

def load_fleet():
    global fleet, fleet_mtime
    mt = os.path.getmtime(FLEET)
    if mt == fleet_mtime: return
    try: f = json.load(open(FLEET))
    except Exception as e:
        log('fleet.json unreadable (keeping previous list):', e); fleet_mtime = mt; return
    changed = False
    for t in f['tails']:
        t['tail'] = t['tail'].upper().strip()
        if not t.get('hex'):
            try:
                h = faa_hex(t['tail'])
                if h: t['hex'] = h; t.setdefault('source', 'FAA registry (auto)'); changed = True; log('hex', t['tail'], h)
            except Exception as e: log('FAA lookup failed', t['tail'], e)
            time.sleep(1)
        if t.get('hex'): t['hex'] = t['hex'].lower()
    if changed:
        try: json.dump(f, open(FLEET, 'w'), indent=1)
        except Exception as e: log('could not write fleet.json', e)
    fleet = f; fleet_mtime = os.path.getmtime(FLEET)

# ---------------------------------------------------------------- ADS-B fetch with failover + backoff
def _get(tpl, hexes):
    req = urllib.request.Request(tpl.format(','.join(hexes)), headers={'User-Agent': UA, 'Accept-Encoding': 'gzip'})
    with urllib.request.urlopen(req, timeout=20) as r:
        b = r.read()
        if r.headers.get('Content-Encoding') == 'gzip': b = gzip.decompress(b)
    j = json.loads(b)
    if not isinstance(j, dict) or not isinstance(j.get('ac', []), list): raise ValueError('unexpected response shape')
    if 'ac' not in j and 'now' not in j: raise ValueError('response missing "ac"')
    return [a for a in (j.get('ac') or []) if isinstance(a, dict)]

def fetch(hexes):
    """Returns (source_name, aircraft_list, error). Tries the healthiest source first; a source that fails is
    backed off exponentially (30 s .. 10 min). A chunk that fails on one source is retried on the next."""
    now = time.time()
    order = sorted(SOURCES, key=lambda s: (src_health[s[0]]['retryAt'] > now, src_health[s[0]]['fails']))
    out, used, errs = [], [], []
    for i in range(0, len(hexes), 100):
        chunk = hexes[i:i + 100]; got = False
        for name, tpl in order:
            h = src_health[name]
            if h['retryAt'] > now and any(src_health[n]['retryAt'] <= now for n, _ in SOURCES): continue
            try:
                out += _get(tpl, chunk); got = True
                h.update(fails=0, retryAt=0, lastOk=time.time())
                if name not in used: used.append(name)
                break
            except Exception as e:
                h['fails'] += 1; h['lastError'] = f'{type(e).__name__}: {e}'[:200]
                h['retryAt'] = time.time() + min(600, 30 * 2 ** (h['fails'] - 1))
                errs.append(f'{name}: {h["lastError"]}')
        if not got: return None, out, '; '.join(errs) or 'all sources failed'
        time.sleep(1.2)
    return '+'.join(used), out, ('; '.join(errs) or None)

def num(v): return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None

# ---------------------------------------------------------------- events
def ap_brief(ap):
    return {k: ap[k] for k in ('code', 'iata', 'name', 'city', 'region', 'country', 'distNm', 'lat', 'lon') if k in ap} if ap else None

def add_event(p, kind, ts, ap, how, inferred, new_events):
    e = {'hex': p['hex'], 'tail': p.get('tail'), 'kind': kind, 'ts': round(float(ts), 3), 'ap': ap_brief(ap),
         'lat': p.get('lat'), 'lon': p.get('lon'), 'altFt': p.get('altFt'), 'how': how, 'inferred': bool(inferred)}
    k = (e['hex'], kind, e['ts'])
    if k in event_keys or not event_ok(e): return e
    event_keys.add(k); events.append(e); new_events.append(e)
    if len(events) > EVENTS_MEM: del events[:len(events) - EVENTS_MEM]
    info = dict(ap_brief(ap) or {}, ts=e['ts'], how=how, inferred=bool(inferred))
    if kind == 'landing': p['lastLanding'] = info
    else: p['lastTakeoff'] = info; p['from'] = info
    log('event', kind, p.get('tail'), (ap or {}).get('code'), how, time.strftime('%H:%M:%S', time.localtime(ts)))
    return e

def remove_event(e, deleted):
    try: events.remove(e)
    except ValueError: pass
    event_keys.discard((e['hex'], e['kind'], e['ts'])); deleted.append(e)

def recompute_last(p):
    """After a retraction, point lastLanding/lastTakeoff back at the newest remaining event."""
    for kind, key in (('landing', 'lastLanding'), ('takeoff', 'lastTakeoff')):
        last = next((e for e in reversed(events) if e['hex'] == p['hex'] and e['kind'] == kind), None)
        if last: p[key] = dict(last['ap'] or {}, ts=last['ts'], how=last['how'], inferred=last['inferred'])
        else: p.pop(key, None)
    if p.get('lastTakeoff'): p['from'] = p['lastTakeoff']
    else: p.pop('from', None)

def is_ground(a, alt, gs):
    if a.get('alt_baro') == 'ground': return True
    # some transponders never set the ground bit: very slow and right at an airport counts as on the ground
    if gs is not None and gs < 35 and (alt is None or alt < 9000):
        ap = nearest_airport(a['lat'], a['lon'], 2.5, 1)
        if ap: return True
    return False

def process_sample(p, a, src, now, new_events, deleted):
    ts = now - (num(a.get('seen_pos')) or 0)
    gs = num(a.get('gs'))
    altb = a.get('alt_baro')
    alt = num(altb) if altb != 'ground' else None
    if alt is None and altb != 'ground': alt = num(a.get('alt_geom'))
    ground = is_ground(a, alt, gs)
    prev_ts = p.get('ts')
    p.update({'lat': a['lat'], 'lon': a['lon'], 'ts': ts, 'onGround': ground, 'altFt': None if ground else alt, 'gsKt': gs,
              'track': num(a.get('track')) if num(a.get('track')) is not None else num(a.get('true_heading')),
              'vsFpm': num(a.get('baro_rate')) if num(a.get('baro_rate')) is not None else num(a.get('geom_rate')),
              'callsign': (a.get('flight') or '').strip() or None, 'squawk': a.get('squawk'), 'type': a.get('t') or p.get('type'), 'source': src})
    p.setdefault('firstSeen', ts)
    low = ground or (alt is not None and alt < 4000)
    ap_close = nearest_airport(a['lat'], a['lon'], 6, 1) if low else None
    p['near'] = ap_close or nearest_airport(a['lat'], a['lon'], 60, 2)
    if low and ap_close and (ground or (p.get('vsFpm') or 0) < 0): p['lastAirport'] = dict(ap_close, ts=ts)
    phase = p.get('phase')
    if ground:
        gap = nearest_airport(a['lat'], a['lon'], 5, 1)
        if phase == 'air':
            add_event(p, 'landing', ts, gap, 'ground-flag', False, new_events)
        elif phase == 'lost-air':
            add_event(p, 'landing', ts, gap, 'reappeared-on-ground', True, new_events)
        elif phase == 'ground' and p.get('groundAirport') and gap and gap['code'] != p['groundAirport'].get('code') \
                and prev_ts and ts - prev_ts > 1800:
            # parked somewhere else than last time with nobody hearing the trip: landed unseen
            add_event(p, 'landing', ts, gap, 'moved-while-unheard', True, new_events)
        if gap: p['groundAirport'] = dict(gap, ts=ts)
        p['phase'] = 'ground'; p['pendingInferred'] = None
    else:
        pend = p.get('pendingInferred')
        if phase == 'ground' and pend and prev_ts and ts - prev_ts < RETRACT_S:
            # we guessed "landed" after losing the signal, but it is still flying: withdraw that guess
            e = next((x for x in reversed(events) if x['hex'] == p['hex'] and x['kind'] == 'landing' and x['ts'] == pend), None)
            if e: remove_event(e, deleted); recompute_last(p); log('retracted inferred landing', p.get('tail'))
            p['phase'] = 'air'; p['pendingInferred'] = None
        elif phase == 'ground':
            origin = p.get('groundAirport')
            oa = AP_BY_CODE.get((origin or {}).get('code'))
            far = bool(oa and nm(a['lat'], a['lon'], oa[6], oa[7]) > 60)
            unheard = prev_ts is not None and ts - prev_ts > 900
            climbing_near = alt is not None and alt < 4000 and (p.get('vsFpm') or 0) > 300 and ap_close
            if (far or origin is None) and climbing_near:
                add_event(p, 'takeoff', ts, ap_close, 'first-seen-climbing', True, new_events)
            elif far and unheard and ts - prev_ts > 6 * 3600:
                pass  # parked long ago somewhere else, now flying far away: real origin unknown, record nothing
            else:
                if origin is None and alt is not None and alt < 3000: origin = ap_close
                add_event(p, 'takeoff', ts, origin, 'first-seen-airborne' if unheard else 'ground-flag', unheard or far, new_events)
            p['phase'] = 'air'; p['pendingInferred'] = None
        elif phase is None:
            # first sample ever and already flying: if it is low, climbing and near a field, call it a takeoff
            if alt is not None and alt < 2500 and (p.get('vsFpm') or 0) > 300 and ap_close:
                add_event(p, 'takeoff', ts, ap_close, 'first-seen-climbing', True, new_events)
            p['phase'] = 'air'
        else:
            p['phase'] = 'air'
    tr = p.get('trail') or []
    if ground: tr = []
    else:
        if tr and ts - tr[-1][2] > 1800: tr = []
        if not tr or tr[-1][2] < ts - 20: tr.append([round(a['lat'], 4), round(a['lon'], 4), int(ts), alt])
        tr = tr[-TRAIL_MAX:]
    p['trail'] = tr

def check_signal_loss(p, now, new_events):
    if p.get('phase') != 'air' or not p.get('ts') or now - p['ts'] < LOSS_S: return False
    alt = p.get('altFt')
    ap = None
    if alt is None or alt < 3500:  # jets land at real airports: prefer a medium/large field within 10 nm
        ap = nearest_airport(p.get('lat'), p.get('lon'), 10, 2) or nearest_airport(p.get('lat'), p.get('lon'), 5, 1)
    if ap:
        # last heard low and close to an airport, then nothing: almost always a landing below receiver coverage
        e = add_event(p, 'landing', p['ts'] + 60, ap, 'signal-loss', True, new_events)
        p['phase'] = 'ground'; p['groundAirport'] = dict(ap, ts=e['ts']); p['pendingInferred'] = e['ts']
    else:
        p['phase'] = 'lost-air'
    return True

# ---------------------------------------------------------------- polling
def poll_once():
    load_fleet()
    tails = {t['hex']: t for t in fleet['tails'] if t.get('hex')}
    src, acs, err = fetch(sorted(tails))
    now = time.time()
    new_events, deleted, changed = [], [], {}
    with lock:
        state['lastPoll'] = now; state['polls'] += 1
        state['lastError'] = err if src is None else None   # a failover that still got data is not an error
        state['lastFailover'] = err if src is not None else state.get('lastFailover')
        if src is not None:
            state['lastSuccess'] = now; state['lastSource'] = src; state['lastCount'] = 0
            seen = set()
            for a in acs:
                try:
                    h = (a.get('hex') or '').lower().lstrip('~')
                    if h not in tails or num(a.get('lat')) is None or num(a.get('lon')) is None or h in seen: continue
                    if (num(a.get('seen_pos')) or 0) > 300: continue  # position too old to be useful
                    seen.add(h)
                    p = state['planes'].get(h) or {'hex': h}
                    p['tail'] = tails[h]['tail']
                    process_sample(p, a, src, now, new_events, deleted)
                    state['planes'][h] = p; changed[h] = p
                except Exception as e:
                    log('sample error', a.get('hex'), e)
            state['lastCount'] = len(seen)
            for h, p in state['planes'].items():
                try:
                    if h not in seen and check_signal_loss(p, now, new_events): changed[h] = p
                except Exception as e: log('loss check error', h, e)
        else:
            state['pollErrors'] += 1
        snap = {h: {k: v for k, v in p.items()} for h, p in changed.items()}
    if snap or new_events or deleted: store.save(planes=snap, events=new_events, deleted=deleted)

def poller():
    while True:
        t0 = time.time()
        try: poll_once()
        except BaseException as e:  # never let the poller thread die
            log('poll error', e); traceback.print_exc()
            state['lastError'] = f'{type(e).__name__}: {e}'[:300]; state['pollErrors'] += 1
        time.sleep(max(5, POLL_S - (time.time() - t0)))

# ---------------------------------------------------------------- history load / merge
def merge_plane(a, b):
    """Merge two copies of one plane's state: newest position wins, newest landing/takeoff wins."""
    if not a: return dict(b)
    if not b: return dict(a)
    base, other = (dict(a), b) if (a.get('ts') or 0) >= (b.get('ts') or 0) else (dict(b), a)
    for k in ('lastLanding', 'lastTakeoff', 'from', 'groundAirport', 'lastAirport'):
        if (other.get(k) or {}).get('ts', 0) > (base.get(k) or {}).get('ts', 0): base[k] = other[k]
    if other.get('firstSeen') and (not base.get('firstSeen') or other['firstSeen'] < base['firstSeen']): base['firstSeen'] = other['firstSeen']
    return base

purge = []
def event_ok(e):
    """Drop impossible events (position far from the airport it was matched to), e.g. from stale seed data."""
    ap = e.get('ap') or {}
    if e.get('lat') is None or not ap: return True
    a = AP_BY_CODE.get(ap.get('code'))
    alat, alon = (ap['lat'], ap['lon']) if 'lat' in ap else (a[6], a[7]) if a else (None, None)
    return alat is None or nm(e['lat'], e['lon'], alat, alon) <= 150

def on_backend_load(planes, evs, name):
    with lock:
        for h, p in (planes or {}).items():
            if isinstance(p, dict): state['planes'][h] = merge_plane(state['planes'].get(h), p)
        added = 0
        for e in evs or []:
            try:
                if not event_ok(e): purge.append(e); continue
                k = (e['hex'], e['kind'], round(float(e['ts']), 3))
                if k in event_keys: continue
                event_keys.add(k); events.append(e); added += 1
            except Exception: pass
        if added:
            events.sort(key=lambda e: e['ts'])
            if len(events) > EVENTS_MEM: del events[:len(events) - EVENTS_MEM]
        return {h: dict(p) for h, p in state['planes'].items()}, list(events)

def seed_from_committed_state():
    if not os.path.exists(SEED_STATE): return
    try:
        s = json.load(open(SEED_STATE))
        for h, p in (s.get('planes') or {}).items():
            p.pop('_wasGround', None)
            if 'phase' not in p: p['phase'] = 'ground' if p.get('onGround') else 'lost-air'
            state['planes'][h] = merge_plane(state['planes'].get(h), p)
    except Exception as e: log('seed state load failed', e)

store = Store(RUNTIME, on_backend_load)

# ---------------------------------------------------------------- HTTP
def public_plane(p):
    return {k: v for k, v in p.items() if not k.startswith('_') and k != 'pendingInferred'}

def health():
    now = time.time(); ls = state.get('lastSuccess')
    age = round(now - ls, 1) if ls else None
    status = 'ok' if age is not None and age < POLL_S * 4 else 'stale' if age is not None and age < 600 else 'down'
    if age is None and now - state['started'] < 120: status = 'starting'
    return {'status': status, 'lastSuccessAgeSec': age, 'lastPollAgeSec': round(now - state['lastPoll'], 1) if state.get('lastPoll') else None,
            'source': state.get('lastSource'), 'lastError': state.get('lastError'), 'pollSeconds': POLL_S,
            'aircraftHeardLastPoll': state.get('lastCount'), 'trackedTails': len(fleet['tails']), 'polls': state['polls'],
            'pollErrors': state['pollErrors'], 'uptimeSec': round(now - state['started']), 'events': len(events),
            'sources': {n: {'fails': h['fails'], 'backoffSec': max(0, round(h['retryAt'] - now)), 'lastOkAgeSec': round(now - h['lastOk']) if h['lastOk'] else None, 'lastError': h['lastError']} for n, h in src_health.items()},
            'storage': store.describe()}

class H(SimpleHTTPRequestHandler):
    def __init__(s, *a, **k): super().__init__(*a, directory=PUB, **k)
    def log_message(s, *a): pass
    def send_json(s, obj, code=200, extra=None):
        b = json.dumps(obj, separators=(',', ':')).encode()
        s.send_response(code); s.send_header('Content-Type', 'application/json'); s.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items(): s.send_header(k, v)
        s.send_header('Access-Control-Allow-Origin', '*'); s.send_header('Content-Length', str(len(b))); s.end_headers(); s.wfile.write(b)
    def end_headers(s):
        if not s.path.startswith('/api/') and s.path != '/healthz': s.send_header('Cache-Control', 'no-cache')
        super().end_headers()
    def do_HEAD(s):
        if s.path.split('?')[0] == '/healthz':
            s.send_response(200); s.send_header('Content-Length', '0'); s.end_headers(); return
        return super().do_HEAD()
    def do_GET(s):
        try: return s.route()
        except (BrokenPipeError, ConnectionResetError): pass
        except Exception as e:
            log('http error', s.path, e); traceback.print_exc()
            try: s.send_json({'error': str(e)}, 500)
            except Exception: pass
    def route(s):
        u = urllib.parse.urlparse(s.path); path = u.path; q = urllib.parse.parse_qs(u.query)
        if path == '/healthz':
            h = health(); return s.send_json(h, 503 if h['status'] == 'down' else 200)
        if path == '/api/fleet':
            return s.send_json({'operators': fleet['operators'], 'tails': fleet['tails']})
        if path == '/api/state':
            with lock:
                planes = {p['tail']: public_plane(p) for p in state['planes'].values() if p.get('tail')}
                h = health()
            return s.send_json({'now': time.time(), 'pollSeconds': POLL_S, 'lastPoll': state['lastPoll'], 'lastSuccess': state.get('lastSuccess'),
                                'source': state['lastSource'], 'error': state['lastError'], 'health': h, 'planes': planes})
        if path == '/api/events':
            tail = (q.get('tail') or [''])[0].upper(); limit = min(int((q.get('limit') or ['50'])[0]), 5000)
            with lock: ev = [e for e in events if not tail or e.get('tail') == tail][-limit:]
            return s.send_json({'events': ev[::-1]})
        if path == '/api/export':
            with lock: dump = {'format': 'jt1-history-v1', 'exported': time.time(), 'planes': {h: dict(p) for h, p in state['planes'].items()}, 'events': list(events)}
            return s.send_json(dump, extra={'Content-Disposition': 'attachment; filename="jet-tracker-1-history.json"'})
        return super().do_GET()

def keepalive():
    """Hit the public URL so Render's free instance does not idle out (skipped without RENDER_EXTERNAL_URL)."""
    base = (os.environ.get('RENDER_EXTERNAL_URL') or '').rstrip('/')
    if not base: return
    url = base + '/healthz'
    time.sleep(60)
    while True:
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'ParaFlightJetTracker1-keepalive/1.0'})
            with urllib.request.urlopen(req, timeout=30) as r: log('keepalive', r.status)
        except urllib.error.HTTPError as e: log('keepalive', e.code)
        except Exception as e: log('keepalive failed', e)
        time.sleep(600)

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get('PORT', '8135'))
    seed_from_committed_state()
    store.start()
    try: load_fleet()
    except Exception as e: log('fleet load failed', e)
    if purge:
        log('purging', len(purge), 'impossible events')
        with lock:
            for p in state['planes'].values():
                if any(x['hex'] == p.get('hex') for x in purge): recompute_last(p)
        store.save(deleted=list(purge), planes={x['hex']: dict(state['planes'][x['hex']]) for x in purge if x['hex'] in state['planes']})
    threading.Thread(target=poller, daemon=True).start()
    if os.environ.get('RENDER_EXTERNAL_URL'): threading.Thread(target=keepalive, daemon=True).start()
    log(f'ParaFlight Jet Tracker 1 on http://0.0.0.0:{port}  storage={list(store.status)}')
    ThreadingHTTPServer(('0.0.0.0', port), H).serve_forever()
