#!/usr/bin/env python3
"""
FPL Draft bot — Draft Footballers Society (league 22578).

Runs hourly on GitHub Actions and works out for itself whether there is
anything to post:

  PREVIEW  in the hours just after a gameweek deadline, once starting XIs
           have unlocked
  ROUNDUP  once a gameweek is finished and bonus points have settled

Each post becomes a GitHub Issue, which emails you. The text is already
formatted for WhatsApp — copy and paste.

No terminal needed. Everything configurable is in the CONFIG block.
"""

import os
import sys
import json
import argparse
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import requests

# ─────────────────────────── CONFIG ───────────────────────────
LEAGUE_ID = 22578

# Post the preview within this many hours of the deadline.
PREVIEW_WINDOW_HOURS = 6

# Nicknames and stadiums. Add more as Jack supplies them — the key is the
# team name exactly as it appears in the game.
NICKNAMES = {"Beautiful Boys XI": "The Beauties"}
STADIUMS = {"Beautiful Boys XI": "the Hello Kitty\u2122 Stadium"}

# The API abbreviates some names oddly. Add any others you spot.
NAME_FIXES = {
    "A.Becker": "Alisson", "O.Dango": "Ouattara", "R\u00faben": "R\u00faben Dias",
    "E.Le F\u00e9e": "Le F\u00e9e", "Kroupi.Jr": "Kroupi", "Virgil": "Van Dijk",
    "Matheus N.": "Matheus Nunes", "B.Fernandes": "Bruno Fernandes",
    "Bruno G.": "Bruno Guimar\u00e3es",
}
# ──────────────────────────────────────────────────────────────

BASE = "https://draft.premierleague.com/api"
POSN = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}
FLAGS = {"i": "injured", "s": "suspended", "d": "doubtful", "u": "unavailable"}
KINDS = {"w": "waiver", "f": "free agent"}
RULE = "\u2014\u2014\u2014"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "application/json",
})

GH_TOKEN = os.environ.get("GITHUB_TOKEN")
GH_REPO = os.environ.get("GITHUB_REPOSITORY")


def log(m):
    print(m, file=sys.stderr)


def get(path, required=True):
    url = f"{BASE}/{path}"
    try:
        r = SESSION.get(url, timeout=25)
    except requests.RequestException as e:
        log(f"STATUS  {path}: failed - {e}")
        if required:
            sys.exit(1)
        return None
    if r.status_code == 200:
        try:
            d = r.json()
            log(f"STATUS  {path}: ok")
            return d
        except json.JSONDecodeError:
            log(f"STATUS  {path}: non-JSON (login page?)")
    else:
        log(f"STATUS  {path}: HTTP {r.status_code}")
    if required:
        sys.exit(1)
    return None


# ─────────────────────────── GitHub ───────────────────────────

def existing_titles():
    if not (GH_TOKEN and GH_REPO):
        return set()
    out, page = set(), 1
    while page <= 10:
        r = requests.get(
            f"https://api.github.com/repos/{GH_REPO}/issues",
            headers={"Authorization": f"Bearer {GH_TOKEN}",
                     "Accept": "application/vnd.github+json"},
            params={"state": "all", "per_page": 100, "page": page}, timeout=25)
        if r.status_code != 200:
            log(f"STATUS  issues list: HTTP {r.status_code}")
            return out
        b = r.json()
        if not b:
            break
        out.update(i["title"] for i in b)
        page += 1
    return out


def post_issue(title, body):
    if not (GH_TOKEN and GH_REPO):
        print(f"\n===== {title} =====\n\n{body}")
        return
    r = requests.post(
        f"https://api.github.com/repos/{GH_REPO}/issues",
        headers={"Authorization": f"Bearer {GH_TOKEN}",
                 "Accept": "application/vnd.github+json"},
        json={"title": title, "body": body}, timeout=25)
    log(f"POSTED  {title}" if r.status_code == 201
        else f"STATUS  issue create: HTTP {r.status_code} {r.text[:200]}")


# ───────────────────────── data helpers ───────────────────────

def player_index(bs):
    clubs = {t["id"]: t["short_name"] for t in bs["teams"]}
    return {e["id"]: {
        "name": NAME_FIXES.get(e["web_name"], e["web_name"]),
        "pos": POSN.get(e["element_type"], "?"),
        "club": clubs.get(e["team"], "???"), "team": e["team"],
        "rank": e.get("draft_rank"), "status": e.get("status", "a"),
        "news": (e.get("news") or "").strip(),
    } for e in bs["elements"]}


def parse_dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def nick(team):
    return NICKNAMES.get(team, team)


def picks_for(entry_id, gw):
    """(starting, bench, subs) with auto-subs applied to the starting XI."""
    d = get(f"entry/{entry_id}/event/{gw}", required=False)
    if not d:
        return None, None, None
    start = [p["element"] for p in d.get("picks", []) if p.get("position", 99) <= 11]
    bench = [p["element"] for p in d.get("picks", []) if p.get("position", 99) > 11]
    subs = d.get("subs") or []
    # A benched player who came on DID score; the starter he replaced did not.
    for s in subs:
        i, o = s.get("element_in"), s.get("element_out")
        if i in bench and o in start:
            start[start.index(o)] = i
            bench[bench.index(i)] = o
    return start, bench, subs


def fmt_xi(ids, P):
    g = defaultdict(list)
    for e in ids:
        p = P.get(e, {})
        g[p.get("pos", "?")].append(f"{p.get('name','?')} ({p.get('club','?')})")
    shape = "-".join(str(len(g[k])) for k in ("DEF", "MID", "FWD"))
    lines = [f"({shape})"]
    for k in ("GK", "DEF", "MID", "FWD"):
        if g[k]:
            lines.append(f"{k} — " + ", ".join(sorted(g[k])))
    return lines


def flagged(ids, P):
    out = []
    for e in ids:
        p = P.get(e, {})
        if p.get("status", "a") != "a":
            out.append(f"{p['name']} — "
                       f"{p['news'] or FLAGS.get(p['status'], p['status'])}")
    return out


def stacks(ids, P, n=3):
    c = defaultdict(list)
    for e in ids:
        c[P.get(e, {}).get("club", "?")].append(P.get(e, {}).get("name", "?"))
    return {k: v for k, v in c.items() if len(v) >= n}


def moves_by_entry(gw, P):
    """Moves keyed by ENTRY_ID.

    The transactions endpoint's `entry` field is the entry_id (114770), NOT
    the league_entry id (115216). Verified against real data. Do not
    'correct' this without checking the raw JSON first.
    """
    d = get(f"draft/league/{LEAGUE_ID}/transactions", required=False)
    if not d:
        return None
    out = defaultdict(list)
    for t in d.get("transactions", []):
        if t.get("result") != "a" or t.get("event", 0) != gw:
            continue
        i = P.get(t["element_in"], {}).get("name", "?")
        o = P.get(t["element_out"], {}).get("name", "?")
        out[t.get("entry")].append(
            f"{i} in, {o} out ({KINDS.get(t.get('kind'), t.get('kind','move'))})")
    return out


def h2h(matches, a, b, before):
    w, pts = defaultdict(int), defaultdict(int)
    dr = pl = 0
    for m in matches:
        if not m.get("finished") or m.get("event", 0) >= before:
            continue
        if {m["league_entry_1"], m["league_entry_2"]} != {a, b}:
            continue
        pl += 1
        p1, p2 = m["league_entry_1_points"], m["league_entry_2_points"]
        pts[m["league_entry_1"]] += p1
        pts[m["league_entry_2"]] += p2
        if p1 > p2:
            w[m["league_entry_1"]] += 1
        elif p2 > p1:
            w[m["league_entry_2"]] += 1
        else:
            dr += 1
    return pl, w, dr, pts


# ─────────────────────────── PREVIEW ──────────────────────────

def build_preview(gw, bs, P):
    d = get(f"league/{LEAGUE_ID}/details")
    ent = {e["id"]: e for e in d["league_entries"]}
    eid = {e["id"]: e["entry_id"] for e in d["league_entries"]}
    rank = {s["league_entry"]: s["rank"] for s in d.get("standings", [])}
    mv = moves_by_entry(gw, P)

    fx = [m for m in d["matches"] if m.get("event") == gw]
    if not fx:
        return None

    opp = {}
    for f in bs.get("fixtures", {}).get(str(gw), []):
        opp[f["team_h"]] = f["team_a"]
        opp[f["team_a"]] = f["team_h"]

    L = [f"*GAMEWEEK {gw} — PREVIEW*", ""]
    all_start = {}

    for m in fx:
        a_id, b_id = m["league_entry_1"], m["league_entry_2"]
        A, B = ent.get(a_id), ent.get(b_id)
        if not A or not B:
            continue
        L += [RULE, ""]
        L.append(f"*{nick(A['entry_name'])} v {nick(B['entry_name'])}*")
        rl = ""
        if rank.get(a_id) and rank.get(b_id):
            rl = f" (#{rank[a_id]} v #{rank[b_id]})"
        stad = STADIUMS.get(A["entry_name"])
        L.append(f"{A['player_first_name']} v {B['player_first_name']}{rl}"
                 + (f" — at {stad}" if stad else ""))
        L.append("")

        sides = {}
        for E in (A, B):
            st, bn, _ = picks_for(eid[E["id"]], gw)
            sides[E["id"]] = (st, bn)
            if st:
                all_start[E["id"]] = st
                xi = fmt_xi(st, P)
                L.append(f"*{E['entry_name']}* {xi[0]}")
                L += xi[1:]
                L.append("")

        pl, w, dr, pts = h2h(d["matches"], a_id, b_id, gw)
        if pl:
            L.append(f"*Season head-to-head:* played {pl} — "
                     f"{A['entry_name']} {w[a_id]}, {B['entry_name']} {w[b_id]}"
                     + (f", {dr} drawn" if dr else "")
                     + f" (points {pts[a_id]}–{pts[b_id]})")
            L.append("")

        sa, sb = sides[a_id][0], sides[b_id][0]
        if sa and sb and opp:
            seen, con = set(), []
            for ea in sa:
                ta = P[ea]["team"]
                for eb in sb:
                    tb = P[eb]["team"]
                    if opp.get(ta) == tb and (ta, tb) not in seen:
                        seen.add((ta, tb))
                        ga = [P[x]["name"] for x in sa if P[x]["team"] == ta]
                        gb = [P[x]["name"] for x in sb if P[x]["team"] == tb]
                        con.append(f"{P[ea]['club']} v {P[eb]['club']} — "
                                   f"{', '.join(ga)} against {', '.join(gb)}")
            if con:
                L += ["*On the pitch:* " + "; ".join(con), ""]

        fl = []
        for E in (A, B):
            fl += [f"{x} ({E['entry_name']})" for x in flagged(sides[E["id"]][0] or [], P)]
        if fl:
            L += ["*Flagged:* " + "; ".join(fl), ""]

        bw = []
        for E in (A, B):
            bn = sides[E["id"]][1]
            if bn:
                best = min(bn, key=lambda e: P[e].get("rank") or 999)
                bw.append(f"{E['entry_name']} leaves out {P[best]['name']} "
                          f"(ranked {P[best]['rank']})")
        if bw:
            L += ["*Benched:* " + "; ".join(bw), ""]

        sk_any = False
        for E in (A, B):
            sk = stacks(sides[E["id"]][0] or [], P)
            if sk:
                sk_any = True
                L.append(f"*{E['entry_name']} stacks:* " + "; ".join(
                    f"{len(v)} {c} ({', '.join(v)})" for c, v in sk.items()))
        if sk_any:
            L.append("")

        if mv:
            ml = [f"{E['entry_name']}: " + "; ".join(mv[E["entry_id"]])
                  for E in (A, B) if mv.get(E["entry_id"])]
            if ml:
                L += ["*Moves:* " + " | ".join(ml), ""]

    if all_start:
        cc = defaultdict(int)
        for st in all_start.values():
            for e in st:
                cc[P[e]["club"]] += 1
        top = sorted(cc.items(), key=lambda x: -x[1])[:3]
        L += [RULE, "", "*AROUND THE LEAGUE*", "",
              "*Most-started clubs:* " + ", ".join(f"{c} ({n})" for c, n in top), ""]

    return "\n".join(L)


# ─────────────────────────── ROUNDUP ──────────────────────────

def build_roundup(gw, bs, P):
    d = get(f"league/{LEAGUE_ID}/details")
    ent = {e["id"]: e for e in d["league_entries"]}
    eid = {e["id"]: e["entry_id"] for e in d["league_entries"]}

    done = [m for m in d["matches"] if m.get("event") == gw and m.get("finished")]
    if not done:
        return None

    live = get(f"event/{gw}/live", required=False)
    pts = {}
    if live:
        pts = {int(k): v.get("stats", {}).get("total_points", 0)
               for k, v in live.get("elements", {}).items()}

    L = [f"*GAMEWEEK {gw} — ROUNDUP*", "", RULE, "", "*RESULTS*", ""]
    for m in done:
        A, B = ent.get(m["league_entry_1"]), ent.get(m["league_entry_2"])
        if A and B:
            L.append(f"{nick(A['entry_name'])} {m['league_entry_1_points']} – "
                     f"{m['league_entry_2_points']} {nick(B['entry_name'])}")

    L += ["", RULE, "", "*STANDINGS*", ""]
    for s in d.get("standings", []):
        e = ent.get(s["league_entry"])
        if e:
            L.append(f"{s['rank']}. {e['entry_name']} — "
                     f"{s['matches_won']}-{s['matches_drawn']}-{s['matches_lost']}, "
                     f"{s['total']} pts ({s['points_for']} for)")

    squads = {}
    if pts:
        for E in d["league_entries"]:
            st, bn, subs = picks_for(eid[E["id"]], gw)
            if st:
                squads[E["id"]] = (st, bn or [], subs or [])

    # ---- per-fixture write-ups ----
    if squads:
        L += ["", RULE, "", "*THE GAMES*", ""]
        for m in done:
            a_id, b_id = m["league_entry_1"], m["league_entry_2"]
            A, B = ent.get(a_id), ent.get(b_id)
            if not (A and B and a_id in squads and b_id in squads):
                continue
            pa, pb = m["league_entry_1_points"], m["league_entry_2_points"]
            win, lose = (A, B) if pa > pb else (B, A)
            wp, lp = max(pa, pb), min(pa, pb)
            margin = wp - lp

            L.append(f"*{nick(A['entry_name'])} {pa} – {pb} {nick(B['entry_name'])}*")
            if margin == 0:
                L.append("Dead level.")
            elif margin <= 3:
                L.append(f"{nick(win['entry_name'])} by {margin}.")
            elif margin >= 25:
                L.append(f"{nick(win['entry_name'])} by {margin} — never in doubt.")
            else:
                L.append(f"{nick(win['entry_name'])} by {margin}.")

            for E in (A, B):
                st, bn, _ = squads[E["id"]]
                top = sorted(((pts.get(e, 0), P[e]["name"]) for e in st),
                             reverse=True)[:3]
                blanks = [P[e]["name"] for e in st if pts.get(e, 0) <= 0]
                bits = ["led by " + ", ".join(f"{n} ({p})" for p, n in top)]
                if blanks:
                    bits.append(f"{len(blanks)} blank"
                                + ("s" if len(blanks) > 1 else "")
                                + f" ({', '.join(blanks[:3])})")
                left = sum(pts.get(e, 0) for e in bn)
                if left:
                    best_b = max(bn, key=lambda e: pts.get(e, 0))
                    bits.append(f"{left} left on the bench, "
                                f"{P[best_b]['name']} the pick of them")
                L.append(f"{E['entry_name']}: " + "; ".join(bits) + ".")

            # would the bench have changed it?
            lose_bench = sum(pts.get(e, 0) for e in squads[lose["id"]][1])
            if lose_bench > margin:
                L.append(f"{nick(lose['entry_name'])} had {lose_bench} on the "
                         f"bench and lost by {margin}. Work that one out.")
            L.append("")

    if pts:
        scored = []
        for lid, (st, bn, _) in squads.items():
            nm = ent[lid]["entry_name"]
            for e in st:
                scored.append((pts.get(e, 0), P[e]["name"], P[e]["club"], nm))
        if scored:
            scored.sort(reverse=True)
            p, n, c, t = scored[0]
            L += [RULE, "", f"*STAR MAN* — {n} ({c}), {p} pts, for {t}", "",
                  "*Top starters:* " + "; ".join(f"{n} {p}" for p, n, c, t in scored[:5]),
                  "",
                  "*Lowest starters:* " + "; ".join(f"{n} {p}" for p, n, c, t in scored[-5:]),
                  ""]
        regret = sorted(((sum(pts.get(e, 0) for e in bn), ent[lid]["entry_name"])
                         for lid, (st, bn, _) in squads.items()), reverse=True)
        if regret:
            L += ["*Points left on the bench*", ""]
            for v, nm in regret:
                L.append(f"{nm} — {v}")
            L.append("")

    mv = moves_by_entry(gw, P)
    if mv:
        by_entry_id = {e["entry_id"]: e for e in d["league_entries"]}
        L += [RULE, "", "*MOVES*", ""]
        for eidk, lines in mv.items():
            nm = by_entry_id.get(eidk, {}).get("entry_name", "Unknown")
            for x in lines:
                L.append(f"{nm}: {x}")
        L.append("")

    return "\n".join(L)


# ──────────────────────────── main ────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["auto", "preview", "roundup"], default="auto")
    ap.add_argument("--gw", type=int)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    bs = get("bootstrap-static")
    P = player_index(bs)
    events = bs["events"]["data"]
    now = datetime.now(timezone.utc)
    log(f"Now (UTC): {now:%Y-%m-%d %H:%M}")

    jobs = []
    if a.mode in ("preview", "roundup") and a.gw:
        jobs = [(a.mode, a.gw)]
    else:
        for e in events:
            dl = parse_dt(e["deadline_time"])
            if dl <= now < dl + timedelta(hours=PREVIEW_WINDOW_HOURS):
                jobs.append(("preview", e["id"]))
        fin = [e for e in events if e.get("finished")]
        if fin:
            jobs.append(("roundup", max(fin, key=lambda e: e["id"])["id"]))
        if a.mode == "preview":
            jobs = [j for j in jobs if j[0] == "preview"]
        elif a.mode == "roundup":
            jobs = [j for j in jobs if j[0] == "roundup"]

    if not jobs:
        log("Nothing to do.")
        return

    seen = set() if a.dry_run else existing_titles()
    for kind, gw in jobs:
        title = f"GW{gw} {kind}"
        if title in seen:
            log(f"SKIP    {title} — already posted")
            continue
        body = (build_preview if kind == "preview" else build_roundup)(gw, bs, P)
        if not body:
            log(f"SKIP    {title} — no data yet")
            continue
        if a.dry_run:
            print(f"\n===== {title} =====\n\n{body}")
        else:
            post_issue(title, body)


if __name__ == "__main__":
    main()
