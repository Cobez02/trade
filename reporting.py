"""
Reporting: build the live HTML dashboard + the plain-text email digest
from the durable state file. Self-contained (inline SVG chart, no CDN).
"""
from __future__ import annotations
import datetime as dt, html, json

from engine import SLEEVES, RETIRED_SLEEVES, SLEEVE_ALLOCATION, START_EQUITY

SLEEVE_LABEL = {
    "wsb":  "Reddit / WSB sentiment",
    "news": "News catalysts",
    "tech": "Technical indicators",
    "flow": "Options-flow copy",
}

# Retired sleeves stay on the dashboard: their history is part of the record,
# and a row that silently vanished would read as a broken feed, not a decision.
RETIRED_NOTE = {
    "wsb":  "retired — entry signal replaced by the crowd veto",
    "flow": "retired — OI skew is unsigned; no signal without open/close data",
}

# dark-mode palette (validated slots)
C_BOT   = "#3987e5"   # series 1 blue
C_SPX   = "#d95926"   # series 2 orange
C_GOOD  = "#0ca30c"
C_BAD   = "#d03b3b"
SURFACE = "#1a1a19"; PLANE = "#0d0d0d"; INK = "#ffffff"; INK2 = "#c3c2b7"
MUTED = "#898781"; GRID = "#2c2c2a"; AXIS = "#383835"


def _pct(x):
    return f"{x*100:+.2f}%"

def _money(x):
    return f"${x:,.0f}" if abs(x) >= 100 else f"${x:,.2f}"

def _color_for(x):
    return C_GOOD if x >= 0 else C_BAD


def compute_kpis(state: dict) -> dict:
    hist = state.get("equity_history", [])
    start_eq = state.get("start_equity", START_EQUITY) or START_EQUITY
    if hist:
        equity = hist[-1]["equity"]
        bench = hist[-1]["benchmark_equity"]
    else:
        equity, bench = start_eq, START_EQUITY
    bot_ret = equity / start_eq - 1
    spx_ret = bench / START_EQUITY - 1
    closed = state.get("closed", [])
    realized = [c for c in closed if c.get("pnl") is not None]
    wins = [c for c in realized if c["pnl"] > 0]
    return {
        "equity": equity, "start_eq": start_eq, "bot_ret": bot_ret,
        "spx_ret": spx_ret, "alpha": bot_ret - spx_ret,
        "open_n": len(state.get("positions", {})),
        "closed_n": len(closed), "realized_n": len(realized),
        "win_rate": (len(wins) / len(realized)) if realized else None,
        "started": state.get("started"),
    }


def sleeve_rows(state: dict):
    rows = []
    for s in SLEEVES:
        closed = [c for c in state.get("closed", []) if c.get("sleeve") == s]
        realized = [c for c in closed if c.get("pnl") is not None]
        wins = [c for c in realized if c["pnl"] > 0]
        pnl = sum(c["pnl"] for c in realized)
        open_ct = sum(1 for m in state.get("positions", {}).values() if m.get("sleeve") == s)
        open_cost = sum(m.get("entry_cost", 0) for m in state.get("positions", {}).values()
                        if m.get("sleeve") == s)
        rows.append({
            "sleeve": s, "label": SLEEVE_LABEL[s], "pnl": pnl,
            "ret": pnl / SLEEVE_ALLOCATION, "open": open_ct, "open_cost": open_cost,
            "trades": len(realized),
            "win_rate": (len(wins) / len(realized)) if realized else None,
            "retired": s in RETIRED_SLEEVES,
        })
    return rows


# ---------------------------------------------------------------------------
def _svg_equity(hist, w=920, h=280, pad=44):
    if len(hist) < 1:
        return '<div class="empty">Equity curve appears after the first trading day.</div>'
    xs = list(range(len(hist)))
    bot = [p["equity"] for p in hist]
    spx = [p["benchmark_equity"] for p in hist]
    lo = min(min(bot), min(spx)); hi = max(max(bot), max(spx))
    if hi == lo:
        hi = lo + 1
    span = hi - lo
    lo -= span * 0.08; hi += span * 0.08
    def X(i): return pad + (w - 2 * pad) * (i / max(len(hist) - 1, 1))
    def Y(v): return pad + (h - 2 * pad) * (1 - (v - lo) / (hi - lo))
    def path(series):
        return "M" + " L".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(series))
    # gridlines (5 horizontal)
    grid = []
    for k in range(5):
        gy = pad + (h - 2 * pad) * k / 4
        val = hi - (hi - lo) * k / 4
        grid.append(f'<line x1="{pad}" y1="{gy:.1f}" x2="{w-pad}" y2="{gy:.1f}" stroke="{GRID}" stroke-width="1"/>')
        grid.append(f'<text x="{pad-8}" y="{gy+4:.1f}" text-anchor="end" fill="{MUTED}" font-size="11">${val:,.0f}</text>')
    # x labels (first, mid, last)
    xlab = []
    for i in [0, len(hist)//2, len(hist)-1]:
        if 0 <= i < len(hist):
            xlab.append(f'<text x="{X(i):.1f}" y="{h-pad+18:.1f}" text-anchor="middle" fill="{MUTED}" font-size="11">{hist[i]["date"][5:]}</text>')
    end_bot = f'<circle cx="{X(len(hist)-1):.1f}" cy="{Y(bot[-1]):.1f}" r="4" fill="{C_BOT}"/>'
    end_spx = f'<circle cx="{X(len(hist)-1):.1f}" cy="{Y(spx[-1]):.1f}" r="4" fill="{C_SPX}"/>'
    return f'''<svg viewBox="0 0 {w} {h}" width="100%" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Equity vs S&P 500">
      {''.join(grid)}
      <line x1="{pad}" y1="{h-pad}" x2="{w-pad}" y2="{h-pad}" stroke="{AXIS}" stroke-width="1"/>
      <path d="{path(spx)}" fill="none" stroke="{C_SPX}" stroke-width="2" stroke-linejoin="round"/>
      <path d="{path(bot)}" fill="none" stroke="{C_BOT}" stroke-width="2" stroke-linejoin="round"/>
      {end_bot}{end_spx}
      {''.join(xlab)}
    </svg>'''


# The learner emits a sizing multiplier bounded to [GATE_FLOOR, GATE_CEIL] =
# [0.25, 1.25]. It is never zero and it never blocks. Two things follow for this
# panel, and both were wrong before:
#
#   * "paused" is unreachable. The old code tested `w == 0.0`, which no longer
#     occurs, so the dashboard would have silently stopped reporting the state it
#     was built to report. Worse, it would have implied a capability the bot
#     deliberately does not have.
#   * The bar was scaled `w / 1.8`, a leftover from an unbounded weight. Against
#     a 1.25 ceiling every bar would render between 14% and 69% of the track and
#     a maxed-out sleeve would look two-thirds full.
#
# Bars are now scaled against GATE_CEIL, so full width means "as encouraged as
# the learner is permitted to be", and a tick marks neutral (1.0) at 80%.
W_FLOOR, W_CEIL = 0.25, 1.25
W_NEUTRAL = 1.0


def _weight_style(w: float, n: int):
    """(fill fraction, colour, label) for a sleeve weight."""
    try:
        w = float(w)
    except (TypeError, ValueError, OverflowError):
        w = W_NEUTRAL
    if not (w == w) or w in (float("inf"), float("-inf")):   # NaN / inf
        w = W_NEUTRAL
    w = max(W_FLOOR, min(W_CEIL, w))
    frac = max(0.03, min(1.0, w / W_CEIL))
    if not n:
        return frac, MUTED, "exploring"
    if w < 0.999:
        return frac, (C_BAD if w <= 0.5 else C_BOT), f"×{w:g} cut"
    if w > 1.001:
        return frac, C_GOOD, f"×{w:g} up"
    return frac, C_BOT, "neutral"


def _learning_html(state: dict) -> str:
    lr = state.get("learning", {}) or {}
    sleeves = lr.get("sleeve", {}) or {}
    lessons = lr.get("lessons", []) or []
    n = lr.get("n_trades", 0)
    if not n:
        return ('<div class="empty">The learner is still gathering data — it forms '
                'conclusions once trades settle. Weights start neutral; sleeves and '
                'setups with evidence against them are sized down, never switched '
                'off, so they keep producing the evidence that could clear them.</div>')

    n_eff = lr.get("n_eff")
    n_days = lr.get("n_days")
    act = lr.get("diagnostics", {}).get("activation_n_eff", 30)
    if isinstance(n_eff, (int, float)):
        if n_eff < act:
            hdr = (f"{n} settled trades over {n_days} session(s) — effective sample "
                   f"{n_eff:.1f} after same-day clustering. Sizing stays neutral "
                   f"until {act}.")
            hdr_col = MUTED
        else:
            hdr = (f"{n} settled trades over {n_days} session(s) — effective sample "
                   f"{n_eff:.1f}. Sizing is active.")
            hdr_col = INK2
    else:
        hdr, hdr_col = f"{n} settled trades.", MUTED

    bars = ""
    for s in SLEEVES:
        st = sleeves.get(s, {}) or {}
        nn = st.get("n", 0)
        frac, col, status = _weight_style(st.get("weight", 1.0), nn)
        if s in RETIRED_SLEEVES:
            # Weight is still learned from the sleeve's history but no longer
            # sizes anything — entry skips retired sleeves entirely.
            col, status = MUTED, "retired"
        ne = st.get("n_eff")
        eff_txt = f" · n<sub>eff</sub> {ne:.1f}" if isinstance(ne, (int, float)) and nn else ""
        bars += f'''<div class="wbar">
          <div class="wbar-lbl">{SLEEVE_LABEL[s]} <span style="color:{MUTED}">({nn}{eff_txt})</span></div>
          <div class="wbar-track"><div class="wbar-fill" style="width:{frac*100:.0f}%;background:{col}"></div><div class="wbar-tick"></div></div>
          <div class="wbar-val" style="color:{col}">{status}</div>
        </div>'''

    n_gates = len(lr.get("gates", []) or [])
    gate_txt = (f"{n_gates} setup(s) sized down" if n_gates else "no setup sized down")
    scale = (f'Scale: {W_FLOOR:g}× floor → {W_CEIL:g}× ceiling, tick at neutral. '
             f'The floor is not zero: nothing is ever blocked, because a blocked '
             f'setup can never generate the evidence that would clear it. '
             f'Currently {gate_txt}.')

    lessons_html = "".join(f"<li>{html.escape(l)}</li>" for l in lessons) or "<li>No conclusions yet.</li>"
    return f'''<div style="color:{hdr_col};font-size:12px;margin-bottom:10px">{html.escape(hdr)}</div>
      <div class="wbars">{bars}</div>
      <div style="color:{MUTED};font-size:11px;margin-top:8px">{scale}</div>
      <div style="color:{MUTED};font-size:11px;margin:12px 0 6px;text-transform:uppercase;letter-spacing:.06em">Lessons learned</div>
      <ul class="notes">{lessons_html}</ul>'''


def _tile(label, value, sub, color=INK):
    return f'''<div class="tile">
      <div class="tile-label">{label}</div>
      <div class="tile-value" style="color:{color}">{value}</div>
      <div class="tile-sub">{sub}</div>
    </div>'''


def build_dashboard(state: dict) -> str:
    k = compute_kpis(state)
    hist = state.get("equity_history", [])
    as_of = hist[-1]["date"] if hist else dt.date.today().isoformat()
    verdict = "BEATING" if k["alpha"] >= 0 else "TRAILING"
    verdict_color = C_GOOD if k["alpha"] >= 0 else C_BAD

    tiles = "".join([
        _tile("Portfolio value", _money(k["equity"]),
              f"from {_money(k['start_eq'])} start", INK),
        _tile("Bot return", _pct(k["bot_ret"]), "options, 4 sleeves", _color_for(k["bot_ret"])),
        _tile("S&amp;P 500 (SPY)", _pct(k["spx_ret"]), "buy &amp; hold benchmark", _color_for(k["spx_ret"])),
        _tile("Alpha vs S&amp;P", _pct(k["alpha"]), verdict, verdict_color),
        _tile("Open positions", str(k["open_n"]), f"{k['closed_n']} closed", INK),
        _tile("Win rate", ("—" if k["win_rate"] is None else f"{k['win_rate']*100:.0f}%"),
              f"{k['realized_n']} settled trades", INK),
    ])

    # sleeve table
    srows = ""
    for r in sleeve_rows(state):
        wr = "—" if r["win_rate"] is None else f"{r['win_rate']*100:.0f}%"
        badge = (f' <span style="color:{MUTED};font-size:11px" title='
                 f'"{html.escape(RETIRED_NOTE.get(r["sleeve"], "retired"))}">'
                 f'· retired</span>') if r.get("retired") else ""
        srows += f'''<tr>
          <td class="lbl">{html.escape(r['label'])}{badge}</td>
          <td class="num">{r['open']}</td>
          <td class="num" style="color:{_color_for(r['pnl'])}">{_money(r['pnl'])}</td>
          <td class="num" style="color:{_color_for(r['ret'])}">{_pct(r['ret'])}</td>
          <td class="num">{r['trades']}</td>
          <td class="num">{wr}</td>
        </tr>'''

    # open positions
    prows = ""
    for sym, m in sorted(state.get("positions", {}).items()):
        exp = m.get("expiration", "")
        d = ""
        try:
            d = (dt.date.fromisoformat(exp) - dt.date.today()).days
        except Exception:
            pass
        prows += f'''<tr>
          <td class="mono">{html.escape(m.get('underlying',''))} {str(m.get('type','')).upper()} ${m.get('strike','')}</td>
          <td class="lbl">{SLEEVE_LABEL.get(m.get('sleeve'),'')}</td>
          <td class="num">{m.get('qty','')}</td>
          <td class="num">${m.get('entry_price','')}</td>
          <td class="num">{exp} ({d}d)</td>
          <td class="thesis">{html.escape(str(m.get('thesis','')))}</td>
        </tr>'''
    if not prows:
        prows = '<tr><td colspan="6" class="empty">No open positions.</td></tr>'

    # recent closed
    crows = ""
    for c in reversed(state.get("closed", [])[-12:]):
        pnl = c.get("pnl")
        pnl_s = "—" if pnl is None else _money(pnl)
        col = INK2 if pnl is None else _color_for(pnl)
        crows += f'''<tr>
          <td class="mono">{html.escape(str(c.get('underlying','')))} {str(c.get('type','')).upper()} ${c.get('strike','')}</td>
          <td class="lbl">{SLEEVE_LABEL.get(c.get('sleeve'),'')}</td>
          <td class="num" style="color:{col}">{pnl_s}</td>
          <td class="lbl">{html.escape(str(c.get('exit_reason','')))}</td>
          <td class="num">{c.get('closed_on','')}</td>
        </tr>'''
    if not crows:
        crows = '<tr><td colspan="5" class="empty">No closed trades yet.</td></tr>'

    # last run notes
    log = state.get("run_log", [])
    notes = log[-1]["notes"] if log else []
    notes_html = "".join(f"<li>{html.escape(n)}</li>" for n in notes) or "<li>No activity.</li>"

    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SPX-Beater — options paper trading</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:{PLANE}; color:{INK};
         font-family: system-ui,-apple-system,"Segoe UI",sans-serif; }}
  .wrap {{ max-width: 1000px; margin: 0 auto; padding: 28px 20px 60px; }}
  h1 {{ font-size: 22px; margin:0 0 2px; letter-spacing:-0.02em; }}
  .sub {{ color:{MUTED}; font-size:13px; margin-bottom:22px; }}
  .banner {{ background:{SURFACE}; border:1px solid rgba(255,255,255,.10);
             border-radius:14px; padding:16px 20px; margin-bottom:18px;
             display:flex; align-items:center; gap:14px; }}
  .banner .big {{ font-size:26px; font-weight:700; }}
  .grid {{ display:grid; grid-template-columns: repeat(3,1fr); gap:12px; margin-bottom:20px; }}
  .tile {{ background:{SURFACE}; border:1px solid rgba(255,255,255,.10);
           border-radius:14px; padding:14px 16px; }}
  .tile-label {{ color:{MUTED}; font-size:12px; }}
  .tile-value {{ font-size:24px; font-weight:700; margin-top:4px; font-variant-numeric:tabular-nums; }}
  .tile-sub {{ color:{INK2}; font-size:12px; margin-top:2px; }}
  .card {{ background:{SURFACE}; border:1px solid rgba(255,255,255,.10);
           border-radius:14px; padding:18px 20px; margin-bottom:18px; }}
  .card h2 {{ font-size:14px; margin:0 0 12px; color:{INK2}; font-weight:600;
             text-transform:uppercase; letter-spacing:.06em; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; }}
  th {{ text-align:left; color:{MUTED}; font-weight:500; padding:6px 8px;
        border-bottom:1px solid {AXIS}; font-size:11px; text-transform:uppercase; letter-spacing:.04em; }}
  td {{ padding:8px; border-bottom:1px solid {GRID}; vertical-align:top; }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
  td.mono {{ font-family: ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; }}
  td.lbl {{ color:{INK2}; }}
  td.thesis {{ color:{MUTED}; font-size:12px; }}
  .legend {{ display:flex; gap:18px; font-size:12px; color:{INK2}; margin:6px 0 4px; }}
  .dot {{ display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:6px; vertical-align:middle; }}
  .empty {{ color:{MUTED}; text-align:center; padding:16px; }}
  ul.notes {{ margin:0; padding-left:18px; color:{INK2}; font-size:13px; }}
  ul.notes li {{ margin:3px 0; }}
  .wbars {{ display:flex; flex-direction:column; gap:8px; }}
  .wbar {{ display:grid; grid-template-columns: 190px 1fr 64px; align-items:center; gap:10px; }}
  .wbar-lbl {{ font-size:12px; color:{INK2}; }}
  .wbar-track {{ position:relative; height:8px; background:{GRID}; border-radius:5px; overflow:hidden; }}
  .wbar-fill {{ height:100%; border-radius:5px; }}
  .wbar-tick {{ position:absolute; left:80%; top:0; width:1px; height:100%; background:{INK2}; opacity:.55; }}
  .wbar-val {{ font-size:12px; text-align:right; font-variant-numeric:tabular-nums; }}
  .disc {{ color:{MUTED}; font-size:11px; margin-top:22px; line-height:1.5; }}
</style></head>
<body><div class="wrap">
  <h1>SPX-Beater · autonomous options paper trading</h1>
  <div class="sub">As of {as_of} · Alpaca paper account · $10k conservative · benchmark = S&amp;P 500 (SPY)</div>

  <div class="banner">
    <div class="dot" style="width:14px;height:14px;background:{verdict_color}"></div>
    <div><span class="big" style="color:{verdict_color}">{verdict} the S&amp;P 500</span>
    &nbsp;by {_pct(k['alpha'])}</div>
  </div>

  <div class="grid">{tiles}</div>

  <div class="card">
    <h2>Equity vs S&amp;P 500</h2>
    <div class="legend">
      <span><span class="dot" style="background:{C_BOT}"></span>Bot (options)</span>
      <span><span class="dot" style="background:{C_SPX}"></span>S&amp;P 500 buy &amp; hold</span>
    </div>
    {_svg_equity(hist)}
  </div>

  <div class="card">
    <h2>Strategy sleeves — which method is winning</h2>
    <table>
      <tr><th>Sleeve</th><th style="text-align:right">Open</th><th style="text-align:right">Realized P&amp;L</th>
      <th style="text-align:right">Return</th><th style="text-align:right">Trades</th><th style="text-align:right">Win %</th></tr>
      {srows}
    </table>
  </div>

  <div class="card">
    <h2>Recursive learning — what's working, what's gated</h2>
    {_learning_html(state)}
  </div>

  <div class="card">
    <h2>Open positions</h2>
    <table>
      <tr><th>Contract</th><th>Sleeve</th><th style="text-align:right">Qty</th>
      <th style="text-align:right">Entry</th><th style="text-align:right">Expiry</th><th>Thesis</th></tr>
      {prows}
    </table>
  </div>

  <div class="card">
    <h2>Recent closed trades</h2>
    <table>
      <tr><th>Contract</th><th>Sleeve</th><th style="text-align:right">P&amp;L</th><th>Reason</th><th style="text-align:right">Closed</th></tr>
      {crows}
    </table>
  </div>

  <div class="card">
    <h2>Last run activity</h2>
    <ul class="notes">{notes_html}</ul>
  </div>

  <div class="disc">Experimental / educational paper-trading system. Trades are simulated on an Alpaca
  paper account with no real money. Signals are generated autonomously from public sentiment, news
  keywords, technical indicators, and options open-interest — none of this is investment advice, and
  paper-trading results do not reflect real-world fills, slippage, or liquidity.</div>
</div></body></html>'''


def build_digest(state: dict) -> str:
    k = compute_kpis(state)
    lines = []
    verdict = "BEATING" if k["alpha"] >= 0 else "TRAILING"
    wr_str = "—" if k["win_rate"] is None else f"{k['win_rate']*100:.0f}%"
    lines.append(f"SPX-Beater daily digest — {dt.date.today().isoformat()}")
    lines.append("")
    lines.append(f"Portfolio: {_money(k['equity'])}  ({_pct(k['bot_ret'])})")
    lines.append(f"S&P 500:   {_pct(k['spx_ret'])}  buy & hold")
    lines.append(f"Alpha:     {_pct(k['alpha'])}  — {verdict} the S&P 500")
    lines.append(f"Open: {k['open_n']} positions | Closed: {k['closed_n']} | Win rate: {wr_str}")
    lines.append("")
    lines.append("By sleeve:")
    for r in sleeve_rows(state):
        lines.append(f"  {r['label']:<26} P&L {_money(r['pnl']):>9}  ({_pct(r['ret'])})  "
                     f"{r['open']} open, {r['trades']} closed")
    lines.append("")
    log = state.get("run_log", [])
    if log and log[-1]["notes"]:
        lines.append("Today's activity:")
        for n in log[-1]["notes"]:
            lines.append(f"  - {n}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    st = json.load(open(sys.argv[1])) if len(sys.argv) > 1 else {}
    open("dashboard.html", "w").write(build_dashboard(st))
    print(build_digest(st))
