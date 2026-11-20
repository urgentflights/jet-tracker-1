"""Persistence for ParaFlight Jet Tracker 1 history (takeoff/landing events + last-seen state per tail).

Backends, all optional and used together when configured:
  * Postgres  - DATABASE_URL   (pure-Python driver pg8000, from requirements.txt)
  * Key Value - KV_URL / REDIS_URL (Render Key Value / Redis; tiny built-in RESP client)
  * File      - data/runtime/history.json (always; ephemeral on Render, durable on the box)
Writes go through a background thread so the poller never blocks on a database. If a backend is down
the app keeps working from memory; the backend is retried with backoff and, once reachable again, its
data is merged into memory and the full in-memory history is written back to it."""
import json, os, queue, socket, ssl, threading, time, urllib.parse

def log(*a): print('[store]', *a, flush=True)

# ---------------------------------------------------------------- Postgres
class PG:
    name = 'postgres'
    SCHEMA = [
        """CREATE TABLE IF NOT EXISTS jt_events (id BIGSERIAL PRIMARY KEY, hex TEXT NOT NULL, tail TEXT,
             kind TEXT NOT NULL, ts DOUBLE PRECISION NOT NULL, airport TEXT, data JSONB NOT NULL,
             created_at TIMESTAMPTZ DEFAULT now(), UNIQUE (hex, kind, ts))""",
        "CREATE INDEX IF NOT EXISTS jt_events_hex_ts ON jt_events (hex, ts DESC)",
        """CREATE TABLE IF NOT EXISTS jt_planes (hex TEXT PRIMARY KEY, tail TEXT, last_seen DOUBLE PRECISION,
             data JSONB NOT NULL, updated_at TIMESTAMPTZ DEFAULT now())""",
    ]
    def __init__(s, url): s.url = url; s.con = None
    def connect(s):
        import pg8000.native as pg
        u = urllib.parse.urlparse(s.url); q = urllib.parse.parse_qs(u.query)
        mode = (q.get('sslmode') or [''])[0]
        host = u.hostname or 'localhost'
        kw = dict(user=urllib.parse.unquote(u.username or ''), password=urllib.parse.unquote(u.password or '') or None,
                  host=host, port=u.port or 5432, database=(u.path or '/').lstrip('/') or None, timeout=15)
        # Render internal hostnames have no dots and need no TLS; external hosts (Render external, Supabase) do.
        if mode != 'disable' and ('.' in host and host not in ('127.0.0.1',) and host != 'localhost' or mode in ('require', 'verify-full')):
            ctx = ssl.create_default_context()
            if mode != 'verify-full': ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
            kw['ssl_context'] = ctx
        s.con = pg.Connection(**kw)
        for q_ in s.SCHEMA: s.con.run(q_)
    def close(s):
        try: s.con and s.con.close()
        except Exception: pass
        s.con = None
    def load(s):
        planes = {r[0]: r[1] if isinstance(r[1], dict) else json.loads(r[1]) for r in s.con.run('SELECT hex, data FROM jt_planes')}
        events = [r[0] if isinstance(r[0], dict) else json.loads(r[0]) for r in s.con.run('SELECT data FROM jt_events ORDER BY ts')]
        return planes, events
    def put_events(s, evs):
        for e in evs:
            s.con.run("""INSERT INTO jt_events (hex, tail, kind, ts, airport, data) VALUES (:h, :t, :k, :ts, :a, CAST(:d AS jsonb))
                         ON CONFLICT (hex, kind, ts) DO UPDATE SET data = EXCLUDED.data, airport = EXCLUDED.airport""",
                      h=e['hex'], t=e.get('tail'), k=e['kind'], ts=float(e['ts']), a=(e.get('ap') or {}).get('code'), d=json.dumps(e))
    def del_events(s, evs):
        for e in evs: s.con.run('DELETE FROM jt_events WHERE hex=:h AND kind=:k AND ts=:ts', h=e['hex'], k=e['kind'], ts=float(e['ts']))
    def put_planes(s, planes):
        for h, p in planes.items():
            s.con.run("""INSERT INTO jt_planes (hex, tail, last_seen, data) VALUES (:h, :t, :ls, CAST(:d AS jsonb))
                         ON CONFLICT (hex) DO UPDATE SET tail=EXCLUDED.tail, last_seen=EXCLUDED.last_seen, data=EXCLUDED.data, updated_at=now()""",
                      h=h, t=p.get('tail'), ls=p.get('ts'), d=json.dumps(p))

# ---------------------------------------------------------------- Key Value (Redis protocol)
class KV:
    name = 'keyvalue'
    def __init__(s, url): s.url = url; s.sock = None
    def connect(s):
        u = urllib.parse.urlparse(s.url)
        sk = socket.create_connection((u.hostname, u.port or 6379), timeout=15)
        if u.scheme == 'rediss': sk = ssl.create_default_context().wrap_socket(sk, server_hostname=u.hostname)
        s.sock = sk; s.f = sk.makefile('rb')
        if u.password: s.cmd('AUTH', *( [urllib.parse.unquote(u.username)] if u.username and u.username != 'default' else []), urllib.parse.unquote(u.password))
        s.cmd('PING')
    def close(s):
        try: s.sock and s.sock.close()
        except Exception: pass
        s.sock = None
    def cmd(s, *args):
        out = [b'*%d\r\n' % len(args)]
        for a in args:
            b = a if isinstance(a, bytes) else str(a).encode()
            out += [b'$%d\r\n' % len(b), b, b'\r\n']
        s.sock.sendall(b''.join(out)); return s._read()
    def _read(s):
        line = s.f.readline()
        if not line: raise ConnectionError('keyvalue connection closed')
        t, rest = line[:1], line[1:-2]
        if t == b'+': return rest.decode()
        if t == b'-': raise RuntimeError(rest.decode())
        if t == b':': return int(rest)
        if t == b'$':
            n = int(rest)
            if n < 0: return None
            d = s.f.read(n + 2); return d[:-2]
        if t == b'*':
            n = int(rest); return None if n < 0 else [s._read() for _ in range(n)]
        raise RuntimeError('bad reply %r' % line)
    def load(s):
        r = s.cmd('HGETALL', 'jt:planes') or []
        planes = {r[i].decode(): json.loads(r[i + 1]) for i in range(0, len(r), 2)}
        ev = {}
        r = s.cmd('HGETALL', 'jt:events') or []
        for i in range(0, len(r), 2): ev[r[i].decode()] = json.loads(r[i + 1])
        return planes, sorted(ev.values(), key=lambda e: e['ts'])
    @staticmethod
    def ekey(e): return '%s|%s|%.3f' % (e['hex'], e['kind'], float(e['ts']))
    def put_events(s, evs):
        for i in range(0, len(evs), 200):
            a = []
            for e in evs[i:i + 200]: a += [s.ekey(e), json.dumps(e)]
            s.cmd('HSET', 'jt:events', *a)
    def del_events(s, evs):
        if evs: s.cmd('HDEL', 'jt:events', *[s.ekey(e) for e in evs])
    def put_planes(s, planes):
        items = list(planes.items())
        for i in range(0, len(items), 100):
            a = []
            for h, p in items[i:i + 100]: a += [h, json.dumps(p)]
            s.cmd('HSET', 'jt:planes', *a)

# ---------------------------------------------------------------- File
class FileStore:
    name = 'file'
    def __init__(s, path): s.path = path; s.planes = {}; s.events = {}
    def connect(s):
        os.makedirs(os.path.dirname(s.path), exist_ok=True)
        if os.path.exists(s.path):
            d = json.load(open(s.path)); s.planes = d.get('planes', {}); s.events = {KV.ekey(e): e for e in d.get('events', [])}
    def close(s): pass
    def load(s): return dict(s.planes), sorted(s.events.values(), key=lambda e: e['ts'])
    def _save(s):
        tmp = s.path + '.tmp'
        json.dump({'planes': s.planes, 'events': sorted(s.events.values(), key=lambda e: e['ts'])[-20000:]}, open(tmp, 'w'))
        os.replace(tmp, s.path)
    def put_events(s, evs):
        for e in evs: s.events[KV.ekey(e)] = e
    def del_events(s, evs):
        for e in evs: s.events.pop(KV.ekey(e), None)
    def put_planes(s, planes): s.planes.update(planes)
    def flush(s): s._save()

# ---------------------------------------------------------------- manager
class Store:
    """on_load(planes, events) is called (from the writer thread) whenever a backend (re)connects,
    and must merge the data into memory and return (all_planes, all_events) to write back."""
    def __init__(s, file_path, on_load):
        s.backends = []
        if os.environ.get('DATABASE_URL'): s.backends.append(PG(os.environ['DATABASE_URL']))
        kv = os.environ.get('KV_URL') or os.environ.get('REDIS_URL')
        if kv: s.backends.append(KV(kv))
        s.backends.append(FileStore(file_path))
        s.on_load = on_load
        s.status = {b.name: {'ok': False, 'error': None, 'since': None} for b in s.backends}
        s.pending = {b.name: {'ev': {}, 'del': {}, 'planes': {}} for b in s.backends}
        s.retry_at = {b.name: 0 for b in s.backends}; s.fails = {b.name: 0 for b in s.backends}
        s.q = queue.Queue(); s.plock = threading.Lock()

    def start(s, initial_timeout=20):
        """Try every backend once (bounded time) so startup loads history, then run the writer thread."""
        done = threading.Event()
        def first():
            for b in s.backends: s._try_connect(b)
            done.set()
        threading.Thread(target=first, daemon=True).start()
        done.wait(initial_timeout)
        threading.Thread(target=s._loop, daemon=True).start()

    def _try_connect(s, b):
        st = s.status[b.name]
        try:
            b.connect(); planes, events = b.load()
            all_planes, all_events = s.on_load(planes, events, b.name)
            with s.plock:
                pd = s.pending[b.name]; pd['planes'].update(all_planes)
                for e in all_events: pd['ev'][KV.ekey(e)] = e
            st.update(ok=True, error=None, since=time.time()); s.fails[b.name] = 0
            log(b.name, 'connected; loaded', len(planes), 'planes,', len(events), 'events')
            return True
        except Exception as e:
            b.close(); s.fails[b.name] += 1
            st.update(ok=False, error=f'{type(e).__name__}: {e}'[:300])
            s.retry_at[b.name] = time.time() + min(600, 15 * 2 ** min(s.fails[b.name], 6))
            log(b.name, 'unavailable:', st['error'])
            return False

    # called by the poller (never blocks)
    def save(s, planes=None, events=None, deleted=None):
        with s.plock:
            for pd in s.pending.values():
                if planes: pd['planes'].update(planes)
                for e in events or []: k = KV.ekey(e); pd['ev'][k] = e; pd['del'].pop(k, None)
                for e in deleted or []: k = KV.ekey(e); pd['ev'].pop(k, None); pd['del'][k] = e
                if len(pd['ev']) > 50000:  # bound memory if a backend is down for days
                    for k in sorted(pd['ev'], key=lambda k: pd['ev'][k]['ts'])[:10000]: pd['ev'].pop(k)
        s.q.put(1)

    def _loop(s):
        while True:
            try: s.q.get(timeout=30)
            except queue.Empty: pass
            time.sleep(0.5)
            for b in s.backends:
                try: s._flush(b)
                except Exception as e: log('flush crashed', b.name, e)

    def _flush(s, b):
        st = s.status[b.name]
        if not st['ok']:
            if time.time() < s.retry_at[b.name] or not s._try_connect(b): return
        with s.plock:
            pd = s.pending[b.name]; batch = {'ev': list(pd['ev'].values()), 'del': list(pd['del'].values()), 'planes': dict(pd['planes'])}
            s.pending[b.name] = {'ev': {}, 'del': {}, 'planes': {}}
        if not (batch['ev'] or batch['del'] or batch['planes']): return
        try:
            if batch['del']: b.del_events(batch['del'])
            if batch['ev']: b.put_events(batch['ev'])
            if batch['planes']: b.put_planes(batch['planes'])
            if hasattr(b, 'flush'): b.flush()
            st['lastWrite'] = time.time()
        except Exception as e:
            st.update(ok=False, error=f'{type(e).__name__}: {e}'[:300]); b.close()
            s.fails[b.name] += 1; s.retry_at[b.name] = time.time() + min(600, 15 * 2 ** min(s.fails[b.name], 6))
            log(b.name, 'write failed, will retry:', st['error'])
            with s.plock:  # put the batch back (newer pending data wins)
                pd = s.pending[b.name]
                for e in batch['ev']: pd['ev'].setdefault(KV.ekey(e), e)
                for e in batch['del']: pd['del'].setdefault(KV.ekey(e), e)
                for h, p in batch['planes'].items(): pd['planes'].setdefault(h, p)

    def describe(s):
        return {n: {k: v for k, v in st.items()} for n, st in s.status.items()}
