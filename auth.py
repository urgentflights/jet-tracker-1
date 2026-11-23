"""Accounts for ParaFlight Jet Tracker 1: sign-up, sign-in, sessions and the admin's user controls.

Records (users, sessions, settings) live in memory and are written through to every configured backend:
Postgres (DATABASE_URL, table jt_auth), Key Value (KV_URL, hashes jt:auth:<kind>) and a local file. On start
the newest copy of each record across all reachable backends wins; a backend that was down is re-synced later.
Deletions are stored as tombstones so a backend that missed one cannot bring a deleted record back.

Passwords: scrypt (n=2^14, r=8, p=1) with a 16-byte per-user salt. Sessions: random 256-bit token in an
HttpOnly/Secure/SameSite=Lax cookie, HMAC-signed with SESSION_SECRET; only its SHA-256 is stored."""
import hashlib, hmac, json, os, secrets, threading, time, uuid
from store import PG, KV, log as _slog

def log(*a): print('[auth]', *a, flush=True)

ADMIN_EMAIL = (os.environ.get('ADMIN_EMAIL') or 'sim@paraflight.aero').strip().lower()
COOKIE = 'jt1_session'
REMEMBER_S = 30 * 86400
SHORT_S = 12 * 3600
SECRET = (os.environ.get('SESSION_SECRET') or '').encode() or secrets.token_bytes(32)
if not os.environ.get('SESSION_SECRET'): log('SESSION_SECRET not set: using a random one (sessions end on restart)')

# ---------------------------------------------------------------- passwords
def hash_pw(pw):
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return 'scrypt$16384$8$1$%s$%s' % (salt.hex(), h.hex())
def check_pw(pw, stored):
    try:
        alg, n, r, p, salt, h = stored.split('$')
        if alg != 'scrypt': return False
        x = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(h) // 2)
        return hmac.compare_digest(x.hex(), h)
    except Exception: return False
_DUMMY = hash_pw(secrets.token_hex(8))  # equalises timing for unknown emails

# ---------------------------------------------------------------- backends
class PGAuth(PG):
    SCHEMA = ["""CREATE TABLE IF NOT EXISTS jt_auth (kind TEXT NOT NULL, id TEXT NOT NULL, updated DOUBLE PRECISION NOT NULL,
                 data JSONB NOT NULL, PRIMARY KEY (kind, id))"""]
    def load_all(s):
        return [(r[0], r[1], r[2] if isinstance(r[2], dict) else json.loads(r[2])) for r in s.con.run('SELECT kind, id, data FROM jt_auth')]
    def put(s, kind, id_, rec):
        s.con.run("""INSERT INTO jt_auth (kind, id, updated, data) VALUES (:k, :i, :u, CAST(:d AS jsonb))
                     ON CONFLICT (kind, id) DO UPDATE SET updated = EXCLUDED.updated, data = EXCLUDED.data
                     WHERE jt_auth.updated <= EXCLUDED.updated""", k=kind, i=id_, u=float(rec['updated']), d=json.dumps(rec))
    def purge(s, kind, id_): s.con.run('DELETE FROM jt_auth WHERE kind=:k AND id=:i', k=kind, i=id_)

class KVAuth(KV):
    KINDS = ('user', 'session', 'setting')
    def load_all(s):
        out = []
        for k in s.KINDS:
            r = s.cmd('HGETALL', 'jt:auth:' + k) or []
            for i in range(0, len(r), 2): out.append((k, r[i].decode(), json.loads(r[i + 1])))
        return out
    def put(s, kind, id_, rec): s.cmd('HSET', 'jt:auth:' + kind, id_, json.dumps(rec))
    def purge(s, kind, id_): s.cmd('HDEL', 'jt:auth:' + kind, id_)

class FileAuth:
    name = 'file'
    def __init__(s, path): s.path = path; s.d = {}
    def connect(s):
        os.makedirs(os.path.dirname(s.path), exist_ok=True)
        if os.path.exists(s.path): s.d = json.load(open(s.path))
    def close(s): pass
    def load_all(s): return [tuple(k.split('|', 1)) + (v,) for k, v in s.d.items()]
    def put(s, kind, id_, rec): s.d[kind + '|' + id_] = rec; s._save()
    def purge(s, kind, id_): s.d.pop(kind + '|' + id_, None); s._save()
    def _save(s):
        tmp = s.path + '.tmp'; json.dump(s.d, open(tmp, 'w')); os.chmod(tmp, 0o600); os.replace(tmp, s.path)

# ---------------------------------------------------------------- store
class Auth:
    def __init__(s, file_path):
        s.lock = threading.RLock()  # guards records and backend connections
        s.backends = []
        if os.environ.get('DATABASE_URL'): s.backends.append(PGAuth(os.environ['DATABASE_URL']))
        kv = os.environ.get('KV_URL') or os.environ.get('REDIS_URL')
        if kv: s.backends.append(KVAuth(kv))
        s.backends.append(FileAuth(file_path))
        s.ok = {b.name: False for b in s.backends}; s.err = {b.name: None for b in s.backends}
        s.recs = {'user': {}, 'session': {}, 'setting': {}}
        s.hits = {}  # rate limiting: key -> [timestamps]

    def start(s):
        with s.lock:
            for b in s.backends: s._connect(b)
        threading.Thread(target=s._loop, daemon=True).start()
        log('backends', {b.name: s.ok[b.name] for b in s.backends}, 'users', len(s.users()))

    def _connect(s, b):
        try:
            b.connect(); rows = b.load_all()
            with s.lock:
                for kind, id_, rec in rows:
                    cur = s.recs.setdefault(kind, {}).get(id_)
                    if not cur or rec.get('updated', 0) > cur.get('updated', 0): s.recs[kind][id_] = rec
                allrecs = [(k, i, r) for k, d in s.recs.items() for i, r in d.items()]
            have = {(k, i): r.get('updated', 0) for k, i, r in rows}
            for k, i, r in allrecs:  # write back anything this backend is missing or has older
                if have.get((k, i), -1) < r.get('updated', 0): b.put(k, i, r)
            s.ok[b.name] = True; s.err[b.name] = None
            return True
        except Exception as e:
            b.close(); s.ok[b.name] = False; s.err[b.name] = f'{type(e).__name__}: {e}'[:200]
            log(b.name, 'unavailable:', s.err[b.name]); return False

    def _loop(s):
        while True:
            time.sleep(60)
            for b in s.backends:
                if not s.ok[b.name]:
                    with s.lock: ok = s._connect(b)
                    if ok: log(b.name, 'reconnected and re-synced')
            s._expire()

    def _write(s, kind, id_, rec):
        with s.lock:
            prev = s.recs[kind].get(id_) or {}
            rec['updated'] = max(time.time(), prev.get('updated', 0) + 1e-3)
            s.recs[kind][id_] = rec
            for b in s.backends:
                if not s.ok[b.name]: continue
                try: b.put(kind, id_, rec)
                except Exception as e:
                    log(b.name, 'write failed, reconnecting:', type(e).__name__, e)
                    b.close(); s._connect(b)  # reconnect re-syncs every newer record, including this one

    def _tomb(s, kind, id_): s._write(kind, id_, {'deleted': True})

    def _expire(s):
        now = time.time()
        with s.lock: dead = [i for i, r in s.recs['session'].items() if not r.get('deleted') and r.get('exp', 0) < now]
        for i in dead: s._tomb('session', i)

    def health(s): return {b.name: {'ok': s.ok[b.name], 'error': s.err[b.name]} for b in s.backends}

    # ---- settings
    def setting(s, key, default=None):
        r = s.recs['setting'].get(key); return default if not r or r.get('deleted') else r.get('value', default)
    def set_setting(s, key, value): s._write('setting', key, {'value': value})

    # ---- users
    def users(s): return [u for u in s.recs['user'].values() if not u.get('deleted')]
    def user(s, uid):
        u = s.recs['user'].get(uid); return None if not u or u.get('deleted') else u
    def by_email(s, email):
        e = (email or '').strip().lower()
        return next((u for u in s.users() if u['email'] == e), None)
    def admin_exists(s): return any(u['role'] == 'admin' for u in s.users())

    def signup(s, name, email, pw, code=''):
        name = (name or '').strip()[:80]; email = (email or '').strip().lower()[:200]
        if not name: return None, 'Please enter your name.'
        if '@' not in email or '.' not in email.split('@')[-1] or ' ' in email: return None, 'Please enter a valid email address.'
        if len(pw or '') < 8: return None, 'Password must be at least 8 characters.'
        if len(pw) > 256: return None, 'Password is too long.'
        role = 'user'
        if email == ADMIN_EMAIL:
            want = os.environ.get('ADMIN_SETUP_CODE') or ''
            if not want: return None, 'Admin sign-up is not enabled on this server.'
            if not hmac.compare_digest((code or '').strip().encode(), want.encode()): return None, 'The admin setup code is not correct.'
            role = 'admin'
        with s.lock:
            if s.by_email(email): return None, 'An account with this email already exists. Sign in instead.'
            status = 'active' if role == 'admin' or not s.setting('requireApproval', False) else 'pending'
            u = {'id': uuid.uuid4().hex, 'name': name, 'email': email, 'pw': hash_pw(pw), 'role': role, 'status': status,
                 'created': time.time(), 'lastLogin': None}
            s._write('user', u['id'], u)
        log('signup', u['id'], role, status)
        return u, None

    def login(s, email, pw):
        u = s.by_email(email)
        if not u: check_pw(pw or '', _DUMMY); return None, 'Wrong email or password.'
        if not check_pw(pw or '', u['pw']): return None, 'Wrong email or password.'
        if u['status'] == 'restricted': return None, 'Your access has been restricted. Contact the site admin.'
        if u['status'] == 'pending': return None, 'Your account is waiting for the admin to approve it.'
        u = dict(u, lastLogin=time.time()); s._write('user', u['id'], u)
        return u, None

    def set_status(s, uid, status):
        u = s.user(uid)
        if not u: return False
        s._write('user', uid, dict(u, status=status))
        if status != 'active': s.kill_sessions(uid)
        return True
    def delete_user(s, uid):
        if not s.user(uid): return False
        s.kill_sessions(uid); s._tomb('user', uid); return True

    # ---- sessions
    def new_session(s, uid, remember):
        tok = secrets.token_urlsafe(32); ttl = REMEMBER_S if remember else SHORT_S
        s._write('session', hashlib.sha256(tok.encode()).hexdigest(), {'uid': uid, 'created': time.time(), 'exp': time.time() + ttl, 'remember': bool(remember)})
        sig = hmac.new(SECRET, tok.encode(), hashlib.sha256).hexdigest()[:32]
        return tok + '.' + sig, (ttl if remember else None)
    def _sid(s, cookie_val):
        try:
            tok, sig = cookie_val.rsplit('.', 1)
            if not hmac.compare_digest(sig, hmac.new(SECRET, tok.encode(), hashlib.sha256).hexdigest()[:32]): return None
            return hashlib.sha256(tok.encode()).hexdigest()
        except Exception: return None
    def session_user(s, cookie_val):
        sid = s._sid(cookie_val or '')
        if not sid: return None
        r = s.recs['session'].get(sid)
        if not r or r.get('deleted') or r.get('exp', 0) < time.time(): return None
        u = s.user(r['uid'])
        if not u or u['status'] != 'active': return None
        return u
    def end_session(s, cookie_val):
        sid = s._sid(cookie_val or '')
        if sid and sid in s.recs['session']: s._tomb('session', sid)
    def kill_sessions(s, uid):
        with s.lock: ids = [i for i, r in s.recs['session'].items() if r.get('uid') == uid and not r.get('deleted')]
        for i in ids: s._tomb('session', i)

    # ---- rate limiting (per key, sliding window)
    def limited(s, key, limit, window):
        now = time.time()
        with s.lock:
            a = [t for t in s.hits.get(key, []) if now - t < window]
            if len(a) >= limit: s.hits[key] = a; return True
            a.append(now); s.hits[key] = a
            if len(s.hits) > 5000:
                for k in [k for k, v in s.hits.items() if not v or now - v[-1] > 3600]: s.hits.pop(k, None)
            return False

def public_user(u):
    return {'id': u['id'], 'name': u['name'], 'email': u['email'], 'role': u['role'], 'status': u['status'],
            'created': u.get('created'), 'lastLogin': u.get('lastLogin')}
