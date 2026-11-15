# ParaFlight Jet Tracker 1

Live map + cards for the Part 135 fleets you work with, grouped and colour-coded by operator.

## Layout
- `server.py` – tiny stdlib-only Python backend. Every 30 s it makes ONE batched request for all tails
  (by ICAO hex) to `api.adsb.lol/v2/hex/…` (fallback `opendata.adsb.fi/api/v2/hex/…`), stores the latest
  and last-known position of each plane in `data/state.json`, works out the nearest airport
  (`data/airports.json`, from OurAirports), and serves `public/` plus `/api/fleet` and `/api/state`.
- `public/` – static front end (MapLibre + OpenFreeMap tiles, PWA manifest + service worker).
- `data/fleet.json` – **the fleet list. Edit this to add operators/tails.**

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
Public repo `urgentflights/jet-tracker-1`, auto-deploys on every push to `main`.
To add an operator/tail: edit `data/fleet.json` on GitHub (pencil icon) and commit – Render redeploys in ~2 min.
Free plan: sleeps after ~15 min without visitors (first load then takes ~30-60 s); disk is ephemeral, so
last-known positions reset to the committed `data/state.json` snapshot on each restart/deploy and only
accumulate while the service is awake.
