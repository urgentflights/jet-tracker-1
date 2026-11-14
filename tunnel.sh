#!/bin/bash
# Optional: free public URL via Cloudflare quick tunnel (no account). Needs outbound TCP/UDP 7844,
# which is BLOCKED on the Grok Bot box; works from a normal home/office machine.
cd "$(dirname "$0")"; bin/cloudflared tunnel --no-autoupdate --url http://localhost:${PORT:-8135}
