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

# Post the preview within this many hours of the deadline. Picks do not
# unlock on the stroke of the deadline, so the bot may skip the first run or
# two inside this window and post on a later one.
PREVIEW_WINDOW_HOURS = 8

# Team nicknames. Add more as they arrive — the key is the team name
# exactly as it appears in the game.
NICKNAMES = {"Beautiful Boys XI": "The Beauties"}

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
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY")

# Where the published archive is written. The workflow commits this folder
# and GitHub Pages serves it. Set to None to turn publishing off.
PUBLISH_DIR = "docs"

# Model for the banter section. See platform.claude.com for current IDs.
BANTER_MODEL = "claude-sonnet-5"

VOICE = """You write a weekly roundup for an eight-man fantasy football draft
league called the Draft Footballers Society. It gets pasted into a WhatsApp
group of friends.

Write 4-6 short lines of closing commentary on the week, given the data below.

Rules:
- Dry and understated. One clause, not three. British English.
- Let the data be the punchline. State a number plainly and stop.
- Refer to each team by the MANAGER'S FIRST NAME (Tom, Jack, Calum...),
  not the team name. Team names are decorative; the names are who people
  actually are.
- Be specific: name the player, the manager, the figure.
- NEVER invent a statistic, player, result or detail. Use only what is given.
  If you are unsure of something, leave it out.
- No hype, no exclamation marks, no emoji, no "DISASTER" energy.
- Tease the decision, not the person. Spread it around.
- Plain text only. Bold is *single asterisks*. No headings, no bullets.
- Do not repeat the scores back; the reader has just read them.

Return only the lines themselves, nothing else."""

REPORT_VOICE = """You write short match reports for an eight-man fantasy football
draft league, for a WhatsApp group of friends.

You will be given several fixtures, separated by a line of ===. For EACH
fixture write ONE paragraph of 2-3 sentences.

Rules:
- Dry, British, understated. No hype, no exclamation marks, no emoji.
- Refer to each team by the MANAGER'S FIRST NAME (Tom, Jack, Calum...),
  not the team name.
- Be specific: name players and figures from the data given.
- NEVER invent a statistic, player, result or detail. Use only what is
  given. If unsure, leave it out.
- Do not restate the scoreline as a bare number; the reader has it above.
- Tease the decision, not the person.
- Plain text. No headings, no bullets, no bold.

Separate your paragraphs with a line containing only ###
Return exactly as many paragraphs as there are fixtures, in the same order,
and nothing else."""


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


# ────────────────────────── publishing ────────────────────────

PAGE_CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --ink:#0E1726; --ink-2:#16223A; --line:#243350;
  --paper:#E8EAF0; --dim:#8A97B1; --signal:#F5B301; --pitch:#4ADE80;
}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--ink);color:var(--paper);
  font:16px/1.6 "IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace}
.wrap{max-width:820px;margin:0 auto;padding:32px 20px 96px}
header{border-bottom:2px solid var(--signal);padding-bottom:20px;margin-bottom:8px}
.eyebrow{font-family:"Barlow Condensed",Arial Narrow,sans-serif;
  letter-spacing:.22em;text-transform:uppercase;font-size:13px;color:var(--signal);margin:0}
h1{font-family:"Barlow Condensed",Arial Narrow,sans-serif;font-weight:700;
  font-size:clamp(38px,9vw,68px);line-height:.94;letter-spacing:-.01em;margin:6px 0 10px}
.sub{color:var(--dim);font-size:14px;margin:0}
.ticker{border-bottom:1px solid var(--line);padding:14px 0;margin-bottom:28px;
  display:flex;flex-wrap:wrap;gap:8px 22px}
.tick{font-size:14px;white-space:nowrap}
.tick b{color:var(--signal);font-weight:600}
article{border-top:1px solid var(--line);padding:26px 0}
article:first-of-type{border-top:none}
.meta{font-family:"Barlow Condensed",Arial Narrow,sans-serif;text-transform:uppercase;
  letter-spacing:.16em;font-size:13px;color:var(--dim);margin:0 0 12px}
.meta .gw{color:var(--pitch)}
pre{white-space:pre-wrap;word-wrap:break-word;margin:0;font:inherit;color:var(--paper)}
pre b{color:var(--signal);font-weight:600}
details>summary{cursor:pointer;list-style:none;color:var(--dim);font-size:14px;
  padding:6px 0}
details>summary::-webkit-details-marker{display:none}
details>summary::before{content:"▸ ";color:var(--signal)}
details[open]>summary::before{content:"▾ "}
summary:focus-visible,a:focus-visible{outline:2px solid var(--signal);outline-offset:3px}
footer{color:var(--dim);font-size:13px;border-top:1px solid var(--line);
  margin-top:40px;padding-top:18px}
@media (prefers-reduced-motion:no-preference){
  article{animation:rise .4s ease both}
  @keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
}
"""


def _esc(t):
    return (t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _bold(t):
    """Turn WhatsApp *bold* into <b>, on escaped text."""
    out, parts = [], t.split("*")
    for i, p in enumerate(parts):
        out.append(f"<b>{p}</b>" if i % 2 else p)
    return "".join(out)


def render_page(posts):
    """posts: newest first, each {title, gw, kind, when, body}"""
    latest = posts[0] if posts else None
    ticks = ""
    if latest:
        for line in latest["body"].split("\n"):
            if "–" in line and not line.startswith("*") and len(line) < 60:
                ticks += f'<span class="tick">{_esc(line)}</span>'
                if ticks.count("<span") >= 4:
                    break
    arts = []
    for i, p in enumerate(posts):
        body = _bold(_esc(p["body"]))
        meta = (f'<p class="meta"><span class="gw">Gameweek {p["gw"]}</span> '
                f'&nbsp;·&nbsp; {p["kind"]} &nbsp;·&nbsp; {p["when"]}</p>')
        if i == 0:
            arts.append(f"<article>{meta}<pre>{body}</pre></article>")
        else:
            arts.append(f"<article>{meta}<details><summary>Read this one</summary>"
                        f"<pre>{body}</pre></details></article>")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Draft Footballers Society</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=IBM+Plex+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>{PAGE_CSS}</style></head>
<body><div class="wrap">
<header>
<p class="eyebrow">Draft Footballers Society</p>
<h1>The Weekly</h1>
<p class="sub">Previews and roundups, posted automatically. Newest first.</p>
</header>
<div class="ticker">{ticks}</div>
{"".join(arts) if arts else "<article><p class='meta'>Nothing posted yet.</p></article>"}
<footer>Generated from the Fantasy Premier League Draft API. Updated whenever a
gameweek starts or finishes.</footer>
</div></body></html>"""


def publish(title, body, gw, kind):
    """Write the post into the published archive."""
    if not PUBLISH_DIR:
        return
    try:
        os.makedirs(PUBLISH_DIR, exist_ok=True)
        store = os.path.join(PUBLISH_DIR, "posts.json")
        posts = []
        if os.path.exists(store):
            with open(store) as f:
                posts = json.load(f)
        posts = [p for p in posts if p.get("title") != title]
        posts.insert(0, {"title": title, "gw": gw, "kind": kind,
                         "when": datetime.now(timezone.utc).strftime("%d %b %Y"),
                         "body": body})
        posts.sort(key=lambda p: (p["gw"], p["kind"] == "roundup"), reverse=True)
        with open(store, "w") as f:
            json.dump(posts, f, indent=1, ensure_ascii=False)
        with open(os.path.join(PUBLISH_DIR, "index.html"), "w") as f:
            f.write(render_page(posts))
        open(os.path.join(PUBLISH_DIR, ".nojekyll"), "w").close()
        log(f"PUBLISH {title} — archive now has {len(posts)} posts")
    except Exception as e:
        log(f"STATUS  publish: failed - {e}")


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


def ordinal(n):
    """1 -> 1st. Avoids '#1', which GitHub renders as an issue link."""
    if n is None:
        return None
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def who(entry):
    """Manager first name — the primary way teams are referred to."""
    return entry.get("player_first_name") or entry.get("entry_name", "?")


def who_full(entry):
    """Manager first name with the team name after it, for headings."""
    return f"{who(entry)} ({nick(entry.get('entry_name',''))})"


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


def match_reports(blocks):
    """One API call returning a paragraph per fixture. None on any failure."""
    if not ANTHROPIC_KEY or not blocks:
        return None
    payload = "\n===\n".join(blocks)
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": BANTER_MODEL, "max_tokens": 1000,
                  "system": REPORT_VOICE,
                  "messages": [{"role": "user", "content": payload}]},
            timeout=90)
        if r.status_code != 200:
            log(f"STATUS  reports: HTTP {r.status_code} {r.text[:200]}")
            return None
        txt = "\n".join(b.get("text", "") for b in r.json().get("content", [])
                        if b.get("type") == "text")
        paras = [p.strip() for p in txt.split("###") if p.strip()]
        if len(paras) != len(blocks):
            log(f"STATUS  reports: expected {len(blocks)} paragraphs, "
                f"got {len(paras)} — skipping prose")
            return None
        log("STATUS  reports: ok")
        return paras
    except Exception as e:
        log(f"STATUS  reports: failed - {e}")
        return None


def banter(facts):
    """Ask Claude for a short closing section. Returns None on any failure."""
    if not ANTHROPIC_KEY:
        log("STATUS  banter: no ANTHROPIC_API_KEY set, skipping")
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_KEY,
                     "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": BANTER_MODEL, "max_tokens": 500,
                  "system": VOICE,
                  "messages": [{"role": "user", "content": facts}]},
            timeout=60)
        if r.status_code != 200:
            log(f"STATUS  banter: HTTP {r.status_code} {r.text[:200]}")
            return None
        parts = [b.get("text", "") for b in r.json().get("content", [])
                 if b.get("type") == "text"]
        out = "\n".join(p.strip() for p in parts if p.strip())
        log("STATUS  banter: ok" if out else "STATUS  banter: empty response")
        return out or None
    except Exception as e:
        log(f"STATUS  banter: failed - {e}")
        return None


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
    got_any_xi = False

    for m in fx:
        a_id, b_id = m["league_entry_1"], m["league_entry_2"]
        A, B = ent.get(a_id), ent.get(b_id)
        if not A or not B:
            continue
        L += [RULE, ""]
        L.append(f"*{who(A)} v {who(B)}*")
        rl = ""
        if rank.get(a_id) and rank.get(b_id):
            rl = f" ({ordinal(rank[a_id])} v {ordinal(rank[b_id])})"
        L.append(f"{nick(A['entry_name'])} v {nick(B['entry_name'])}{rl}")
        L.append("")

        sides = {}
        for E in (A, B):
            st, bn, _ = picks_for(eid[E["id"]], gw)
            sides[E["id"]] = (st, bn)
            if st:
                got_any_xi = True
            if st:
                all_start[E["id"]] = st
                xi = fmt_xi(st, P)
                L.append(f"*{who(E)}* — {nick(E['entry_name'])} {xi[0]}")
                L += xi[1:]
                L.append("")

        pl, w, dr, pts = h2h(d["matches"], a_id, b_id, gw)
        if pl:
            L.append(f"*Season head-to-head:* played {pl} — "
                     f"{who(A)} {w[a_id]}, {who(B)} {w[b_id]}"
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
            fl += [f"{x} ({who(E)})" for x in flagged(sides[E["id"]][0] or [], P)]
        if fl:
            L += ["*Flagged:* " + "; ".join(fl), ""]

        bw = []
        for E in (A, B):
            bn = sides[E["id"]][1]
            if bn:
                best = min(bn, key=lambda e: P[e].get("rank") or 999)
                bw.append(f"{who(E)} leaves out {P[best]['name']} "
                          f"(ranked {P[best]['rank']})")
        if bw:
            L += ["*Benched:* " + "; ".join(bw), ""]

        sk_any = False
        for E in (A, B):
            sk = stacks(sides[E["id"]][0] or [], P)
            if sk:
                sk_any = True
                L.append(f"*{who(E)} stacks:* " + "; ".join(
                    f"{len(v)} {c} ({', '.join(v)})" for c, v in sk.items()))
        if sk_any:
            L.append("")

        if mv:
            ml = [f"{who(E)}: " + "; ".join(mv[E["entry_id"]])
                  for E in (A, B) if mv.get(E["entry_id"])]
            if ml:
                L += ["*Moves:* " + " | ".join(ml), ""]

    # Picks unlock shortly AFTER the deadline, not on the stroke of it. A
    # preview with no lineups is barely worth reading, so bail out and let
    # the next hourly run try again inside the window.
    if not got_any_xi:
        log("SKIP    preview — no starting XIs available yet, will retry")
        return None

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
            L.append(f"{who(A)} {m['league_entry_1_points']} – "
                     f"{m['league_entry_2_points']} {who(B)}")

    L += ["", RULE, "", "*STANDINGS*", ""]
    # The API returns these sorted, but don't rely on it.
    for s in sorted(d.get("standings", []),
                    key=lambda x: (x.get("rank") is None, x.get("rank") or 0)):
        e = ent.get(s["league_entry"])
        if e:
            L.append(f"{s['rank']}. {who_full(e)} — "
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
        fixture_blocks, insert_at = [], []
        for m in done:
            a_id, b_id = m["league_entry_1"], m["league_entry_2"]
            A, B = ent.get(a_id), ent.get(b_id)
            if not (A and B and a_id in squads and b_id in squads):
                continue
            pa, pb = m["league_entry_1_points"], m["league_entry_2_points"]
            win, lose = (A, B) if pa > pb else (B, A)
            wp, lp = max(pa, pb), min(pa, pb)
            margin = wp - lp

            L.append(f"*{who(A)} {pa} – {pb} {who(B)}*")
            if margin == 0:
                L.append("Dead level.")
            elif margin <= 3:
                L.append(f"{who(win)} by {margin}.")
            elif margin >= 25:
                L.append(f"{who(win)} by {margin} — never in doubt.")
            else:
                L.append(f"{who(win)} by {margin}.")

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
                L.append(f"{who(E)}: " + "; ".join(bits) + ".")

            # would the bench have changed it?
            lose_bench = sum(pts.get(e, 0) for e in squads[lose["id"]][1])
            if lose_bench > margin:
                L.append(f"{who(lose)} had {lose_bench} on the "
                         f"bench and lost by {margin}. Work that one out.")

            # remember where a prose paragraph should go for this fixture
            insert_at.append(len(L))
            fixture_blocks.append("\n".join(
                [f"{who(A)} ({A['entry_name']}) {pa} v {pb} "
                 f"{who(B)} ({B['entry_name']})"]
                + [x for x in L[-4:] if x]))
            L.append("")

        prose = match_reports(fixture_blocks)
        if prose:
            # insert from the bottom up so earlier indices stay valid
            for idx, para in sorted(zip(insert_at, prose), reverse=True):
                L.insert(idx, para)

    if pts:
        scored = []
        for lid, (st, bn, _) in squads.items():
            nm = who(ent[lid])
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
        regret = sorted(((sum(pts.get(e, 0) for e in bn), who(ent[lid]))
                         for lid, (st, bn, _) in squads.items()), reverse=True)
        if regret:
            L += ["*Points left on the bench*", ""]
            for v, nm in regret:
                L.append(f"{nm} — {v}")
            L.append("")

    # Moves live in the PREVIEW, not here — see build_preview().

    body = "\n".join(L)
    extra = banter(
        f"Gameweek {gw} of the season.\n\n{body}\n\n"
        "Write about the league as a whole — the table, patterns across "
        "fixtures, the bench totals. Do NOT repeat points already made in "
        "the match reports above.")
    if extra:
        body += f"\n{RULE}\n\n{extra}\n"
    return body


# ──────────────────────────── main ────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["auto", "preview", "roundup"], default="auto")
    ap.add_argument("--gw", type=int)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="post again even if an issue with that title exists")
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

    seen = set() if (a.dry_run or a.force) else existing_titles()
    if a.force:
        log("FORCE   ignoring the already-posted check")
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
            publish(title, body, gw, kind)


if __name__ == "__main__":
    main()
