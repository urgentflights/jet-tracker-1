# ParaFlight Jet Tracker 1

Live map + cards for the Part 135 fleets you work with, grouped and colour-coded by operator.

## Layout
- `server.py` – stdlib Python backend. Every 30 s it makes one batched request for all tails (by ICAO hex) to
  `api.adsb.lol` with automatic failover to `opendata.adsb.fi` (a failing source is backed off 30 s → 10 min; a
  chunk that fails on one source is retried on the other). It keeps the latest + last-known position of each
  plane, **detects takeoffs and landings**, matches each to the nearest airport (`data/airports.json`, OurAirports)
  and serves `public/` plus the API below. The poller never dies: every error is caught and shown in `/healthz`.
- `store.py` – history persistence (events + last-seen state). See *History storage*.
- `tools/history.py` – JSON export/import of the history (to move databases).
- `public/` – front end (MapLibre + OpenFreeMap tiles, PWA). Map view with an **Active now** panel (sortable,
  tap a row to fly to the plane) and an **All tails** card list; **Table** view with every tail, search,
  operator/type/status filters and sortable columns (tail, operator, type, status, last landed, last seen).
  Header badge "Data X sec old · source" turns amber after 90 s and red after 5 min. Times are shown in the
  viewer's local zone with a zone label.
- `data/fleet.json` – **the fleet list. Edit this to add operators/tails.**
- `data/state.json` – committed seed of last-known positions (read at startup, never written by the server).

## API
- `/api/fleet`, `/api/state` (planes keyed by tail, incl. `lastLanding`, `lastTakeoff`, `phase`, `health`)
- `/api/events?tail=N123AB&limit=50` – newest takeoff/landing events
- `/api/export` – full history dump (JSON)
- `/healthz` – JSON: `status` ok / stale / down / starting, seconds since last successful poll, source health,
  storage health. HTTP 503 only when no poll has succeeded for 10 min.

## Takeoff / landing detection
- **Seen on ADS-B**: the transponder's on-ground flag (or < 35 kt within 2.5 nm of an airport) flips → takeoff
  or landing at the nearest airport within 5 nm.
- **Inferred** (marked "inferred" in the UI): an airborne plane last heard below 3,500 ft within 10 nm of a
  medium/large airport (else 5 nm of any airport) and then silent for 10 min is logged as landed there (it is
  withdrawn if the plane is heard airborne again within 25 min). A plane that was lost at altitude and is next
  heard on the ground gets a landing at that airport; a plane next heard already airborne gets an inferred takeoff
  from where it was last parked ("~" before the airborne time).
- Coverage gaps mean some small-field landings are missed or timed late; tails never heard since tracking started
  show "never seen yet".

## History storage
All configured backends are written in parallel by a background thread; if one is down the app keeps running from
memory and retries with backoff, then merges and re-syncs when it is back.
- `DATABASE_URL` – Postgres (driver `pg8000`, in `requirements.txt`). Tables `jt_events`, `jt_planes` are created
  automatically. Render free Postgres `jet-tracker-1-db` (dpg-dc0amvetlv9c739mh4u0-a, Virginia) **expires
  2026-12-20** (Render free databases last 30 days) – before then export and move it (below).
- `KV_URL` – Render Key Value `jet-tracker-1-kv` (red-dc0anb6tlv9c739mifvg, free, internal URL
  `redis://red-dc0anb6tlv9c739mifvg:6379`). Survives web-service redeploys/restarts; free Key Value keeps data in
  memory only, so it can be lost if Render restarts the Key Value instance itself.
- `data/runtime/history.json` – always written (durable on a normal machine, wiped on Render redeploys).

### Moving the history (e.g. to Supabase before the free Postgres expires)
    python3 tools/history.py export --url https://jet-tracker-1.onrender.com > history.json
    python3 tools/history.py import history.json --db "postgresql://USER:PASS@HOST:5432/postgres?sslmode=require"
Then set the service's `DATABASE_URL` to the new database and redeploy. On startup the app also merges whatever
it finds in Key Value into a new, empty database automatically.

## Adding a tail / operator
Add to `operators`: `{"id":"newop","name":"New Op","base":"TEB","color":"#ff8800"}`
Add to `tails`:     `{"tail":"N123AB","op":"newop","model":"Citation XLS"}`
`hex` is optional – the server looks the Mode S hex up on registry.faa.gov automatically within a minute
(file is re-read whenever it changes; no restart needed).

## Run
    ./run.sh            # http://localhost:8135   (POLL_SECONDS=30, PORT=8135 env vars)

## Hosting
APIs send no CORS headers, so a small backend is required (no pure-static hosting).
Any host that can run `python3 server.py` works (Render, Fly.io, Railway, a VPS, a home PC).
From a normal machine `./tunnel.sh` gives a free trycloudflare.com URL with no account (URL changes each restart).

## Deployed on Render (free plan)
Public repo `urgentflights/jet-tracker-1` (service srv-dbsf9961egvs73dmaqhg). If a push does not deploy, trigger one from the Render dashboard/API.
To add an operator/tail: edit `data/fleet.json` on GitHub (pencil icon) and commit – Render redeploys in ~2 min.
Free plan: would sleep after ~15 min without visitors, so the server pings its own `/healthz` every 10 min
(`RENDER_EXTERNAL_URL`). The disk is ephemeral; history lives in Postgres / Key Value (see above).
