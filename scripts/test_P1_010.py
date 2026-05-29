#!/usr/bin/env python3
"""P1b regression test for Proposal 010 (live-trades).

One-command check that the agent's live-trades tool works as expected. Updated with the real FMP field names (from fmp_probe.py) and the new optional order_id param.

Before running, test for prescence of alpaca_configured:
    curl -s localhost:8000/healthz | python3 -m json.tool | grep alpaca_configured   # → true

Pre-run setup A: get the real ask price for F from Alpaca (yfinance is unreliable; Alpaca is the live source). Run this from the backend/ dir so it picks up the .env
Before running, test for existing position:
    curl -s -N -X POST localhost:8000/api/chat -H 'Content-Type: application/json' \
        -d '{"message":"show me my F position as a live trade card with current P&L","user_id":"demo"}' | tee /tmp/lt.sse

Pre-run setup B: place a paper buy of 1 F at limit = ask + 1% (so it fills instantly), using the limit printed above:
    backend/.venv/bin/python scripts/test_P1_010_setup.py # 1) real ask from Alpaca (yfinance is crumb-401'd; Alpaca is the live source) # 1) real ask from Alpaca (yfinance is crumb-401'd; Alpaca is the live source)

    curl -s -N -X POST localhost:8000/api/chat -H 'Content-Type: application/json' \
        -d '{"message":"I reviewed the ticket and confirm — place a paper buy of 1 F at limit <LIMIT>","user_id":"demo"}' | tee /tmp/lt.sse # 2) confirm a marketable buy (ask + 1% so it fills instantly), using the limit printed above


Run with the backend venv:
    backend/.venv/bin/python scripts/test_P1_010.py

Example output:
```
tool_calls: ['get_open_position']
widget type: live_trade
  order_id=— (optional; monitoring an existing position)
  long 2 F  fill=15.85 current=16.7 pnl=1.7 (5.36%)
  sources: ['Alpaca paper position', 'Real-time quote']
```
"""
import json, re
raw = open('/tmp/lt.sse').read()
print("tool_calls:", re.findall(r'"name": "([a-z_]+)"', raw))
w = None
for fr in re.split(r'\r?\n\r?\n', raw):
    if 'event: widget' in fr:
        m = re.search(r'data: (\{.*\})', fr, re.S)
        if m: w = json.loads(m.group(1))
if not w:
    print("NO widget — tail:", raw[-300:])
else:
    d = w['data']; print("widget type:", w['type'])
    if w['type'] == 'live_trade':
        print(f"  order_id={d.get('order_id', '— (optional; monitoring an existing position)')}")
        print(f"  {d['side']} {d['shares']} {d['ticker']}  fill={d['fill_price']} current={d['current_price']} pnl={d['unrealized_pnl']} ({d['unrealized_pnl_pct']}%)")
    print("  sources:", [s['name'] for s in w['sources']])