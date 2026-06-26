"""Spot Relocator — READ-ONLY solver.

For a contract line whose ordered spots didn't all place under Etere's FCFS
scheduler, find a feasible way to seat the shortfall: either a direct
separation-valid open slot, or by relocating a *moveable* WL spot (same day
first) to free one. Computes everything in memory; writes NOTHING.

Model (verified — see tasks/spot-relocator.md):
  * pod (commercial break) = traffic_segment row Type='COMS'; capacity = MaxDuration
    (per-date override in trf_instancesegment). Air frame = scheduleblock.Offset + segment.Offset.
  * a placed spot attaches to its pod via trafficPalinse.clusterIndex = ID_TrafficSegment.
  * commercial = trafficPalinse.ID_ContrattiRighe > 0. Active = ID_TRAFFICTRASH null/0 & LIVELLO=0.
  * separation is PER-ADVERTISER (CONTRATTITESTATA.COMMITTENTE) across all their contracts.
  * "fits" = MaxDuration - used >= spot duration.

Usage (validation):  uv run python3 browser_automation/spot_relocator.py <line_id>
"""
from __future__ import annotations

import sys
from collections import defaultdict
from datetime import timedelta

sys.path.insert(0, "browser_automation")
from etere_direct_client import connect  # noqa: E402

FPS = 29.97
DAY_COLS = ["LUNEDI", "MARTEDI", "MERCOLEDI", "GIOVEDI", "VENERDI", "SABATO", "DOMENICA"]
MARKET = {1: "NYC", 2: "CMP", 3: "HOU", 4: "SFO", 5: "SEA", 6: "LAX", 7: "CVC", 8: "WDC", 9: "MMT", 10: "DAL"}


def hms(fr):
    s = int(round(fr / FPS)); return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def fmin(fr):  # frames -> minutes
    return fr / FPS / 60


# ── data loaders ────────────────────────────────────────────────────────────

def line_info(cur, line_id):
    cur.execute(
        """SELECT cr.ID_CONTRATTITESTATA, ct.COD_CONTRATTO, ct.COMMITTENTE, ISNULL(a.RAG_SOCIAL,''),
                  cr.COD_USER, cr.ORA_INIZIO, cr.ORA_FINE,
                  cr.LUNEDI,cr.MARTEDI,cr.MERCOLEDI,cr.GIOVEDI,cr.VENERDI,cr.SABATO,cr.DOMENICA,
                  cr.DATA_INIZIO, cr.DATA_FINE, cr.N_PASSAGGI, cr.Interv_Committente,
                  (SELECT COUNT(*) FROM trafficPalinse tp WHERE tp.ID_ContrattiRighe=cr.ID_CONTRATTIRIGHE
                     AND (tp.ID_TRAFFICTRASH=0 OR tp.ID_TRAFFICTRASH IS NULL)) placed,
                  (SELECT TOP 1 p.DURATION FROM trafficPalinse tp JOIN TPALINSE p ON p.ID_TPALINSE=tp.id_tpalinse
                     WHERE tp.ID_ContrattiRighe=cr.ID_CONTRATTIRIGHE) dur
           FROM CONTRATTIRIGHE cr JOIN CONTRATTITESTATA ct ON ct.ID_CONTRATTITESTATA=cr.ID_CONTRATTITESTATA
           LEFT JOIN ANAGRAF a ON a.ID_ANAGRAF=ct.COMMITTENTE
           WHERE cr.ID_CONTRATTIRIGHE=%s""",
        (line_id,),
    )
    r = cur.fetchone()
    if not r:
        return None
    return {
        "line_id": line_id, "contract_id": r[0], "cod_contratto": r[1], "committente": r[2],
        "client": r[3], "cod_user": r[4], "win_lo": int(r[5]), "win_hi": int(r[6]),
        "days": [bool(x) for x in r[7:14]], "flight_start": r[14], "flight_end": r[15],
        "ordered": r[16] or 0, "sep": int(r[17] or 0), "placed": r[18] or 0,
        "dur": int(r[19] or 0),
    }


def eligible_dates(info):
    out, d, end = [], info["flight_start"].date() if hasattr(info["flight_start"], "date") else info["flight_start"], \
        info["flight_end"].date() if hasattr(info["flight_end"], "date") else info["flight_end"]
    while d <= end:
        if info["days"][d.weekday()]:
            out.append(d)
        d += timedelta(days=1)
    return out


def pods(cur, cod_user, d, lo, hi):
    """COMS pods in [lo,hi) on date d for market: list of {sched,block,seg,ora,cap}."""
    cur.execute(
        """SELECT sb.ID_TrafficSchedule, bl.ID_TrafficBlock, seg.ID_TrafficSegment,
                  (sb.offset+seg.Offset) st, seg.MaxDuration
           FROM traffic_calendar ca WITH(NOLOCK)
           JOIN traffic_scheduleblock sb WITH(NOLOCK) ON sb.ID_TrafficSchedule=ca.ID_TrafficSchedule
           JOIN traffic_block bl WITH(NOLOCK) ON bl.ID_TrafficBlock=sb.ID_TrafficBlock
           OUTER APPLY (
             SELECT si.ID_TrafficSegment,si.Offset,si.Type,si.MaxDuration FROM trf_instancesegment si WITH(NOLOCK)
               WHERE si.ID_TrafficBlock=bl.ID_TrafficBlock AND si.COD_USER=ca.Cod_User AND si.INSTANCEDATE=ca.Date AND si.visible=1
             UNION
             SELECT se.ID_TrafficSegment,se.Offset,se.Type,se.MaxDuration FROM traffic_segment se WITH(NOLOCK)
               WHERE se.ID_TrafficBlock=bl.ID_TrafficBlock AND se.visible=1
               AND (SELECT COUNT(*) FROM trf_instancesegment WITH(NOLOCK) WHERE ID_TrafficBlock=bl.ID_TrafficBlock AND COD_USER=ca.Cod_User AND INSTANCEDATE=ca.Date)=0
           ) seg
           WHERE ca.Cod_User=%s AND ca.Date=%s AND bl.expired=0 AND seg.Type='COMS'
             AND (sb.offset+seg.Offset)>=%s AND (sb.offset+seg.Offset)<%s
           ORDER BY st""",
        (cod_user, d, lo, hi),
    )
    rows = [{"sched": r[0], "block": r[1], "seg": r[2], "ora": int(r[3]), "cap": int(r[4] or 0)} for r in cur.fetchall()]
    if not rows:
        return rows
    segs = tuple({p["seg"] for p in rows})
    ph = ",".join(["%s"] * len(segs))
    cur.execute(
        f"""SELECT tp.clusterIndex, SUM(p.DURATION)
            FROM trafficPalinse tp JOIN TPALINSE p ON p.ID_TPALINSE=tp.id_tpalinse
            WHERE p.COD_USER=%s AND p.DATA_PREV=%s AND (tp.ID_TRAFFICTRASH=0 OR tp.ID_TRAFFICTRASH IS NULL)
              AND p.LIVELLO=0 AND tp.clusterIndex IN ({ph})
            GROUP BY tp.clusterIndex""",
        (cod_user, d, *segs),
    )
    used = {r[0]: int(r[1] or 0) for r in cur.fetchall()}
    for p in rows:
        p["used"] = used.get(p["seg"], 0)
        p["free"] = p["cap"] - p["used"]
    return rows


def advertiser_times(cur, committente, cod_user, d):
    """All placed spot air-times (frames) for an advertiser that date+market (sep pool)."""
    cur.execute(
        """SELECT p.ORA FROM trafficPalinse tp JOIN TPALINSE p ON p.ID_TPALINSE=tp.id_tpalinse
           JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE=tp.ID_ContrattiRighe
           JOIN CONTRATTITESTATA ct ON ct.ID_CONTRATTITESTATA=cr.ID_CONTRATTITESTATA
           WHERE p.COD_USER=%s AND p.DATA_PREV=%s AND ct.COMMITTENTE=%s
             AND (tp.ID_TRAFFICTRASH=0 OR tp.ID_TRAFFICTRASH IS NULL) AND p.LIVELLO=0""",
        (cod_user, d, committente),
    )
    return sorted(int(r[0]) for r in cur.fetchall())


def advertiser_spots(cur, committente, cod_user, d):
    """All placed spots for an advertiser that date+market: objects (sep pool + relocation set)."""
    cur.execute(
        """SELECT p.ORA, p.ID_TPALINSE, tp.ID_ContrattiRighe, ct.COD_CONTRATTO,
                  p.DURATION, p.ID_FILMATI, tp.clusterIndex
           FROM trafficPalinse tp JOIN TPALINSE p ON p.ID_TPALINSE=tp.id_tpalinse
           JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE=tp.ID_ContrattiRighe
           JOIN CONTRATTITESTATA ct ON ct.ID_CONTRATTITESTATA=cr.ID_CONTRATTITESTATA
           WHERE p.COD_USER=%s AND p.DATA_PREV=%s AND ct.COMMITTENTE=%s
             AND (tp.ID_TRAFFICTRASH=0 OR tp.ID_TRAFFICTRASH IS NULL) AND p.LIVELLO=0
           ORDER BY p.ORA""",
        (cod_user, d, committente),
    )
    return [{"time": int(r[0]), "tpalinse": r[1], "line": r[2], "contract": r[3],
             "dur": int(r[4] or 0), "filmati": r[5], "seg": r[6],
             "wl": (r[3] or "").upper().startswith("WL")} for r in cur.fetchall()]


def sep_ok(ora, times, sep):
    return all(abs(ora - t) >= sep for t in times)


# ── solver ──────────────────────────────────────────────────────────────────

def solve_line(cur, line_id, verbose=True):
    info = line_info(cur, line_id)
    if not info:
        print(f"line {line_id} not found"); return
    short = info["ordered"] - info["placed"]
    mk = MARKET.get(info["cod_user"], info["cod_user"])
    if verbose:
        print(f"\n{'='*72}\nLine {line_id} | {info['cod_contratto']} | {info['client']} | {mk}")
        print(f"  window {hms(info['win_lo'])}-{hms(info['win_hi'])}  sep {fmin(info['sep']):.0f}min  "
              f"dur {info['dur']/FPS:.0f}s  ordered {info['ordered']} placed {info['placed']} SHORT {short}")
    if short <= 0:
        print("  nothing to place."); return

    line_cache = {info["line_id"]: info}
    def get_line(lid):
        if lid not in line_cache:
            line_cache[lid] = line_info(cur, lid)
        return line_cache[lid]

    for d in eligible_dates(info):
        res = pack_day(cur, info, d, get_line, verbose)
        if res:
            P = res["target"]
            tag = "by repack" if not res.get("evict") else "by evicting a blocker + repack"
            print(f"    ✅ FEASIBLE on {d} ({d.strftime('%a')}) {tag}: seat the stuck spot at "
                  f"{hms(P['ora'])} (seg {P['seg']})")
            if res.get("evict"):
                e, r = res["evict"], res["rehome"]
                trf = "keep creative" if e["filmati"] and e["filmati"] > 0 else "no creative"
                print(f"       ↪ EVICT {e['client']} ({e['contract']}, {e['dur']/FPS:.0f}s, {trf}) "
                      f"from {hms(e['time'])} → re-home {hms(r['pod']['ora'])} "
                      f"(seg {r['pod']['seg']}, free {r['pod']['free']/FPS:.0f}s)")
            if res["moves"]:
                print(f"       requires repositioning {len(res['moves'])} {info['client'].split(' (')[0]} spot(s):")
                for mv in res["moves"]:
                    print(f"        • {mv['contract']} {hms(mv['from'])} → {hms(mv['to'])}")
            elif not res.get("evict"):
                print("       no other spots need to move (direct).")
            return {"date": d, **res}
    print("\n  ⚠ no feasible placement on any eligible day (single-level eviction) → "
          "make-good / wider-window candidate")
    return None


def pack_day(cur, info, d, get_line, verbose=True):
    """Seat the advertiser's in-window spots + 1 new spot into distinct COMS pods, pairwise
    >= sep apart, each within its line window, with capacity. Two move types:
      1. advertiser-repack (reposition the advertiser's own WL spots)
      2. capacity-eviction: free a needed full pod by relocating a moveable WL occupant of a
         DIFFERENT advertiser (single-level), then re-home it (same day, lowest contention).
    Returns {moves, target[, evict, rehome]} or None."""
    sep, lo, hi = info["sep"], info["win_lo"], info["win_hi"]
    adv = advertiser_spots(cur, info["committente"], info["cod_user"], d)
    region = [s for s in adv if lo <= s["time"] < hi]
    if any(not s["wl"] for s in region):
        return None
    fixed_times = [s["time"] for s in adv if not (lo <= s["time"] < hi)]
    plist = pods(cur, info["cod_user"], d, lo, hi)
    own = defaultdict(int)
    for s in region:
        own[s["seg"]] += s["dur"]
    for p in plist:
        p["avail"] = p["cap"] - p["used"] + own.get(p["seg"], 0)

    new_spot = {"tpalinse": "NEW", "line": info["line_id"], "dur": info["dur"],
                "filmati": None, "contract": info["cod_contratto"], "time": None, "seg": None, "wl": True}
    spots = region + [new_spot]
    if verbose:
        times = sorted(p["ora"] for p in plist)
        mx, last = 0, None
        for t in times:
            if last is None or t - last >= sep:
                mx += 1; last = t
        print(f"\n  {d} ({d.strftime('%a')}): {len(plist)} pods (~{mx} sep-spaceable, capacity-blind); "
              f"need {len(spots)} ({len(region)} in-window + new)")

    # 1) plain repack
    res = _solve_pack(spots, plist, fixed_times, sep, get_line, info)
    if res is not None:
        return res

    # 2) single-level capacity-eviction: free one needed pod by moving a moveable WL occupant
    for E, X in _evict_candidates(cur, plist, info, d, get_line):
        saved = E["avail"]
        E["avail"] += X["dur"]
        res = _solve_pack(spots, plist, fixed_times, sep, get_line, info)
        E["avail"] = saved
        if res is None or E["seg"] not in res["used_segs"]:
            continue  # eviction only helps if the repack actually uses the freed pod
        rehome = _find_rehome(cur, X, d, get_line, exclude_segs=res["used_segs"])
        if rehome:
            res["evict"] = X
            res["rehome"] = rehome
            return res
    return None


def _solve_pack(spots, plist, fixed_times, sep, get_line, info):
    """Backtracking assignment of spots -> distinct pods (>= sep apart, in window, capacity)."""
    def cands(s):
        li = get_line(s["line"]) or info
        return [p for p in plist if li["win_lo"] <= p["ora"] < li["win_hi"] and p["avail"] >= s["dur"]]

    order = sorted(range(len(spots)), key=lambda i: len(cands(spots[i])))  # MRV
    assign, used_segs, used_times = {}, set(), list(fixed_times)

    def bt(k):
        if k == len(order):
            return True
        s = spots[order[k]]
        for p in sorted(cands(s), key=lambda p: p["ora"]):
            if p["seg"] in used_segs or not sep_ok(p["ora"], used_times, sep):
                continue
            assign[order[k]] = p; used_segs.add(p["seg"]); used_times.append(p["ora"])
            if bt(k + 1):
                return True
            del assign[order[k]]; used_segs.discard(p["seg"]); used_times.pop()
        return False

    if not bt(0):
        return None
    moves, target = [], None
    for i, s in enumerate(spots):
        p = assign[i]
        if s["tpalinse"] == "NEW":
            target = p
        elif p["ora"] != s["time"]:
            moves.append({"contract": s["contract"], "line": s["line"], "filmati": s["filmati"],
                          "dur": s["dur"], "from": s["time"], "to": p["ora"], "to_seg": p["seg"]})
    return {"moves": moves, "target": target, "used_segs": set(used_segs)}


def _evict_candidates(cur, plist, info, d, get_line):
    """Moveable WL occupants of *other* advertisers in the window pods, ranked by re-home
    freedom (window width × eligible days) — freest first."""
    segs = tuple(p["seg"] for p in plist)
    if not segs:
        return []
    ph = ",".join(["%s"] * len(segs))
    cur.execute(
        f"""SELECT tp.clusterIndex, p.ID_TPALINSE, tp.ID_ContrattiRighe, ct.COD_CONTRATTO,
                   ISNULL(a.RAG_SOCIAL,''), p.DURATION, p.ID_FILMATI, ct.COMMITTENTE, p.ORA
            FROM trafficPalinse tp JOIN TPALINSE p ON p.ID_TPALINSE=tp.id_tpalinse
            JOIN CONTRATTIRIGHE cr ON cr.ID_CONTRATTIRIGHE=tp.ID_ContrattiRighe
            JOIN CONTRATTITESTATA ct ON ct.ID_CONTRATTITESTATA=cr.ID_CONTRATTITESTATA
            LEFT JOIN ANAGRAF a ON a.ID_ANAGRAF=ct.COMMITTENTE
            WHERE p.COD_USER=%s AND p.DATA_PREV=%s AND (tp.ID_TRAFFICTRASH=0 OR tp.ID_TRAFFICTRASH IS NULL)
              AND p.LIVELLO=0 AND tp.clusterIndex IN ({ph})
              AND ct.COD_CONTRATTO LIKE 'WL%%' AND ISNULL(ct.COMMITTENTE,0)<>%s""",
        (info["cod_user"], d, *segs, info["committente"]),
    )
    podby = {p["seg"]: p for p in plist}
    out = []
    for seg, tp, line, cod, cli, dur, fil, comm, ora in cur.fetchall():
        li = get_line(line)
        if not li:
            continue
        freedom = (li["win_hi"] - li["win_lo"]) * max(sum(li["days"]), 1)
        out.append((podby[seg], {"tpalinse": tp, "line": line, "contract": cod, "client": cli,
                                 "dur": int(dur or 0), "filmati": fil, "committente": comm,
                                 "time": int(ora), "from_seg": seg, "freedom": freedom}))
    out.sort(key=lambda x: -x[1]["freedom"])
    return out


def _find_rehome(cur, X, d, get_line, exclude_segs):
    """Where to re-home an evicted spot: same day, its own window, sep-valid for its advertiser,
    capacity, not a pod used by the new packing. Default: lowest contention (most free)."""
    li = get_line(X["line"])
    if not li:
        return None
    xt = advertiser_times(cur, X["committente"], li["cod_user"], d)
    if X["time"] in xt:
        xt.remove(X["time"])  # vacating its current slot
    best = None
    for p in pods(cur, li["cod_user"], d, li["win_lo"], li["win_hi"]):
        if p["seg"] in exclude_segs or p["seg"] == X["from_seg"]:
            continue
        if p["free"] >= X["dur"] and sep_ok(p["ora"], xt, li["sep"]):
            if best is None or p["free"] > best["free"]:
                best = p
    return {"pod": best, "when": "same-day"} if best else None


def plan_line(cur, line_id):
    """Structured (JSON-friendly) plan for one line's first unplaced spot — no printing."""
    info = line_info(cur, line_id)
    if not info:
        return None
    short = info["ordered"] - info["placed"]
    base = {"line_id": line_id, "market": MARKET.get(info["cod_user"], info["cod_user"]),
            "client": (info["client"] or "").split(" (")[0], "contract": info["cod_contratto"],
            "window": f"{hms(info['win_lo'])[:5]}-{hms(info['win_hi'])[:5]}",
            "ordered": info["ordered"], "placed": info["placed"], "short": short,
            "sep_min": round(fmin(info["sep"]))}
    if short <= 0:
        return {**base, "status": "ok"}
    cache = {info["line_id"]: info}
    def gl(lid):
        if lid not in cache:
            cache[lid] = line_info(cur, lid)
        return cache[lid]
    for d in eligible_dates(info):
        res = pack_day(cur, info, d, gl, verbose=False)
        if not res:
            continue
        P = res["target"]
        out = {**base, "date": str(d), "day": d.strftime("%a"), "seat": hms(P["ora"])[:8],
               "moves": [{"contract": m["contract"], "from": hms(m["from"])[:8], "to": hms(m["to"])[:8]}
                         for m in res["moves"]]}
        if res.get("evict"):
            e, r = res["evict"], res["rehome"]
            out["status"] = "evict"
            out["evict"] = {"client": (e["client"] or "").split(" (")[0], "contract": e["contract"],
                            "dur_s": round(e["dur"] / FPS), "from": hms(e["time"])[:8],
                            "to": hms(r["pod"]["ora"])[:8],
                            "keep_creative": bool(e["filmati"] and e["filmati"] > 0)}
        else:
            out["status"] = "repack" if res["moves"] else "direct"
        return out
    return {**base, "status": "makegood"}


def analyze_contract(cur, contract_id):
    """Plan every line with a shortfall on a contract. Read-only; returns JSON-friendly dict."""
    cur.execute("SELECT COD_CONTRATTO FROM CONTRATTITESTATA WHERE ID_CONTRATTITESTATA=%s", (contract_id,))
    r = cur.fetchone()
    if not r:
        return {"error": "Contract not found"}
    cur.execute(
        """SELECT cr.ID_CONTRATTIRIGHE FROM CONTRATTIRIGHE cr
           WHERE cr.ID_CONTRATTITESTATA=%s
             AND cr.N_PASSAGGI > (SELECT COUNT(*) FROM trafficPalinse tp
                  WHERE tp.ID_ContrattiRighe=cr.ID_CONTRATTIRIGHE
                    AND (tp.ID_TRAFFICTRASH=0 OR tp.ID_TRAFFICTRASH IS NULL))
           ORDER BY cr.COD_USER, cr.ID_CONTRATTIRIGHE""",
        (contract_id,),
    )
    lids = [row[0] for row in cur.fetchall()]
    lines = [pl for pl in (plan_line(cur, lid) for lid in lids) if pl]
    return {"contract_id": contract_id, "cod_contratto": str(r[0]).strip(), "lines": lines}


if __name__ == "__main__":
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 80024
    conn = connect(); cur = conn.cursor()
    try:
        solve_line(cur, target)
    finally:
        conn.close()
