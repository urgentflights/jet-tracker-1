#!/usr/bin/env python3
"""Export / import ParaFlight Jet Tracker 1 history (takeoff/landing events + last-seen state) as JSON.

  # dump from the running app (works even though Render's free Postgres has no outside access)
  python3 tools/history.py export --url https://jet-tracker-1.onrender.com > history.json
  # dump straight from a database / key value / local file
  python3 tools/history.py export --db "$DATABASE_URL" > history.json
  python3 tools/history.py export --kv "$KV_URL" > history.json
  python3 tools/history.py export --file data/runtime/history.json > history.json

  # load a dump into any Postgres (Supabase, Neon, a new Render DB...). Tables are created if missing;
  # re-importing is safe (events de-duplicate on tail+kind+time, planes keep the newest copy).
  python3 tools/history.py import history.json --db "postgresql://user:pass@host:5432/db?sslmode=require"
  python3 tools/history.py import history.json --kv "redis://host:6379"

Then point the web service's DATABASE_URL at the new database and redeploy."""
import argparse, json, os, sys, urllib.request
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from store import PG, KV, FileStore

def backend(a):
    if a.db: return PG(a.db)
    if a.kv: return KV(a.kv)
    if a.file: return FileStore(a.file)
    sys.exit('give --db, --kv, --file (or --url for export)')

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', choices=['export', 'import']); ap.add_argument('dump', nargs='?')
    ap.add_argument('--url'); ap.add_argument('--db'); ap.add_argument('--kv'); ap.add_argument('--file')
    a = ap.parse_args()
    if a.cmd == 'export':
        if a.url:
            d = json.load(urllib.request.urlopen(a.url.rstrip('/') + '/api/export', timeout=120))
        else:
            b = backend(a); b.connect(); planes, events = b.load(); b.close()
            d = {'format': 'jt1-history-v1', 'planes': planes, 'events': events}
        json.dump(d, sys.stdout); print(f"exported {len(d['planes'])} planes, {len(d['events'])} events", file=sys.stderr)
    else:
        d = json.load(open(a.dump)) if a.dump and a.dump != '-' else json.load(sys.stdin)
        b = backend(a); b.connect()
        old, _ = b.load()
        planes = {h: p for h, p in d['planes'].items() if (p.get('ts') or 0) >= ((old.get(h) or {}).get('ts') or 0)}
        b.put_events(d['events']); b.put_planes(planes)
        if hasattr(b, 'flush'): b.flush()
        b.close(); print(f"imported {len(planes)} planes, {len(d['events'])} events", file=sys.stderr)

if __name__ == '__main__': main()
