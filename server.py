#!/usr/bin/env python3
"""Jet Tracker 1 - tiny backend: polls free public ADS-B APIs for the tails in data/fleet.json,
remembers last-known positions in data/state.json, serves the static app in public/.
Stdlib only. Run: python3 server.py [port]"""
import json, math, os, re, sys, threading, time, urllib.request, gzip
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(ROOT, 'data'); PUB = os.path.join(ROOT, 'public')
FLEET = os.path.join(DATA, 'fleet.json'); STATE = os.path.join(DATA, 'state.json')
POLL_S = int(os.environ.get('POLL_SECONDS', '30'))
UA = 'JetTracker1/1.0 (small private fleet tracker, ~1 req/30s)'
SOURCES = [('adsb.lol', 'https://api.adsb.lol/v2/hex/{}'),
           ('adsb.fi', 'https://opendata.adsb.fi/api/v2/hex/{}')]
TRAIL_MAX = 400

lock = threading.Lock()
fleet = {'operators': [], 'tails': []}; fleet_mtime = 0
state = {'planes': {}, 'lastPoll': None, 'lastSource': None, 'lastError': None}
if os.path.exists(STATE):
    try: state.update(json.load(open(STATE)))
    except Exception as e: print('state load failed', e)

# ---- airports (OurAirports subset) ----
AP = json.load(open(os.path.join(DATA, 'airports.json')))
def nm(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = math.sin((lat2-lat1)*p/2)**2 + math.cos(lat1*p)*math.cos(lat2*p)*math.sin((lon2-lon1)*p/2)**2
    return 2 * 3440.065 * math.asin(math.sqrt(a))
def nearest_airport(lat, lon, maxnm=40, minsize=1):
    best = None
    for a in AP:
        if a[8] < minsize: continue
        if abs(a[6]-lat) > 0.8 or abs(a[7]-lon) > 1.0: continue
        d = nm(lat, lon, a[6], a[7])
        score = d - (1.5 if a[8] >= 2 else 0)  # prefer real airports over tiny strips when close
        if d <= maxnm and (best is None or score < best[0]): best = (score, d, a)
    if not best: return None
    a = best[2]
    return {'code': a[0], 'iata': a[1], 'name': a[2], 'city': a[3], 'region': a[4], 'country': a[5], 'distNm': round(best[1], 1)}

# ---- FAA registry hex lookup for tails added without hex ----
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
    f = json.load(open(FLEET)); changed = False
    for t in f['tails']:
        t['tail'] = t['tail'].upper().strip()
        if not t.get('hex'):
            try:
                h = faa_hex(t['tail'])
                if h: t['hex'] = h; t.setdefault('source', 'FAA registry (auto)'); changed = True; print('hex', t['tail'], h)
            except Exception as e: print('FAA lookup failed', t['tail'], e)
            time.sleep(1)
        if t.get('hex'): t['hex'] = t['hex'].lower()
    if changed:
        json.dump(f, open(FLEET, 'w'), indent=1)
    fleet = f; fleet_mtime = os.path.getmtime(FLEET)

def fetch(hexes):
    errs = []
    for name, tpl in SOURCES:
        try:
            out = []
            for i in range(0, len(hexes), 100):
                req = urllib.request.Request(tpl.format(','.join(hexes[i:i+100])), headers={'User-Agent': UA, 'Accept-Encoding': 'gzip'})
                r = urllib.request.urlopen(req, timeout=20); b = r.read()
                if r.headers.get('Content-Encoding') == 'gzip': b = gzip.decompress(b)
                j = json.loads(b); out += j.get('ac') or []
                time.sleep(1.2)
            return name, out, None
        except Exception as e: errs.append(f'{name}: {e}')
    return None, [], '; '.join(errs)

def num(v): return v if isinstance(v, (int, float)) else None

def poll_once():
    load_fleet()
    tails = {t['hex']: t for t in fleet['tails'] if t.get('hex')}
    src, acs, err = fetch(sorted(tails))
    now = time.time()
    with lock:
        state['lastPoll'] = now; state['lastSource'] = src; state['lastError'] = err
        if src is None: return
        seen = set()
        for a in acs:
            h = (a.get('hex') or '').lower().lstrip('~')
            if h not in tails or a.get('lat') is None: continue
            seen.add(h)
            ts = now - (num(a.get('seen_pos')) or 0)
            onground = a.get('alt_baro') == 'ground'
            alt = None if onground else num(a.get('alt_baro'))
            if alt is None and not onground: alt = num(a.get('alt_geom'))
            p = state['planes'].get(h) or {}
            prev_air = p.get('onGround') is False
            p.update({'hex': h, 'tail': tails[h]['tail'], 'lat': a['lat'], 'lon': a['lon'], 'ts': ts,
                      'onGround': onground, 'altFt': alt, 'gsKt': num(a.get('gs')), 'track': num(a.get('track')) or num(a.get('true_heading')),
                      'vsFpm': num(a.get('baro_rate')) or num(a.get('geom_rate')), 'callsign': (a.get('flight') or '').strip() or None,
                      'squawk': a.get('squawk'), 'type': a.get('t'), 'source': src})
            low = onground or (alt is not None and alt < 4000)
            ap = (nearest_airport(a['lat'], a['lon'], 6, 1) if low else None) or nearest_airport(a['lat'], a['lon'], 60, 2)
            p['near'] = ap
            if low and ap and ap['distNm'] <= 6:
                if onground or (p.get('vsFpm') or 0) < 0: p['lastAirport'] = dict(ap, ts=ts)
            if onground and ap and ap['distNm'] <= 3: p['groundAirport'] = dict(ap, ts=ts)
            # departure tracking
            if not onground and p.get('_wasGround') and p.get('groundAirport'): p['from'] = p['groundAirport']
            p['_wasGround'] = onground
            tr = p.get('trail') or []
            if onground: tr = []  # reset trail when on ground
            else:
                if not tr or tr[-1][2] < ts - 20: tr.append([round(a['lat'], 4), round(a['lon'], 4), int(ts), alt])
                tr = tr[-TRAIL_MAX:]
            p['trail'] = tr
            state['planes'][h] = p
        tmp = STATE + '.tmp'; json.dump(state, open(tmp, 'w')); os.replace(tmp, STATE)

def poller():
    while True:
        try: poll_once()
        except Exception as e:
            print('poll error', e); state['lastError'] = str(e)
        time.sleep(POLL_S)

class H(SimpleHTTPRequestHandler):
    def __init__(s, *a, **k): super().__init__(*a, directory=PUB, **k)
    def log_message(s, *a): pass
    def send_json(s, obj):
        b = json.dumps(obj, separators=(',', ':')).encode()
        s.send_response(200); s.send_header('Content-Type', 'application/json'); s.send_header('Cache-Control', 'no-store')
        s.send_header('Access-Control-Allow-Origin', '*'); s.send_header('Content-Length', str(len(b))); s.end_headers(); s.wfile.write(b)
    def end_headers(s):
        if not s.path.startswith('/api/'): s.send_header('Cache-Control', 'no-cache')
        super().end_headers()
    def do_GET(s):
        path = s.path.split('?')[0]
        if path == '/api/fleet':
            return s.send_json({'operators': fleet['operators'], 'tails': fleet['tails']})
        if path == '/api/state':
            with lock:
                planes = {}
                for h, p in state['planes'].items():
                    planes[p['tail']] = {k: v for k, v in p.items() if not k.startswith('_')}
                return s.send_json({'now': time.time(), 'pollSeconds': POLL_S, 'lastPoll': state['lastPoll'],
                                    'source': state['lastSource'], 'error': state['lastError'], 'planes': planes})
        return super().do_GET()

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get('PORT', '8135'))
    load_fleet()
    threading.Thread(target=poller, daemon=True).start()
    print(f'Jet Tracker 1 on http://0.0.0.0:{port}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), H).serve_forever()
