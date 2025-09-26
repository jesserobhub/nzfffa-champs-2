# NZFFFA weekly recap PDF generator (Sleeper -> PDF)
# Features: Standings, SOS (opponents' avg points), All-Play%, Expected Wins, Luck (with 🍀/😬/⚖️),
#           League Average SOS row, and rotating praise/roast banter.
#
# Deps: pip install requests reportlab

import os, re, random
from statistics import mean
from typing import Dict, List, Tuple
import requests

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors

# ========= CONFIG =========
LEAGUE_ID = os.getenv("SLEEPER_LEAGUE_ID", "1180242943416172544")
TITLE_PREFIX = "NZFFFA Championship"
# ==========================

def get(url: str):
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()

def load_data(league_id: str):
    league = get(f"https://api.sleeper.app/v1/league/{league_id}")
    users  = get(f"https://api.sleeper.app/v1/league/{league_id}/users")
    rosters= get(f"https://api.sleeper.app/v1/league/{league_id}/rosters")

    last_scored = int(league.get("settings", {}).get("last_scored_leg", 1))
    start_week  = int(league.get("settings", {}).get("start_week", 1))
    weeks = list(range(start_week, last_scored + 1))

    owner_to_team = {}
    for u in users:
        tn = (u.get("metadata", {}) or {}).get("team_name")
        owner_to_team[u["user_id"]] = (tn.strip() if tn else u["display_name"].strip())

    roster_to_owner = {r["roster_id"]: r["owner_id"] for r in rosters}
    roster_to_team  = {rid: owner_to_team.get(oid, f"Roster {rid}") for rid, oid in roster_to_owner.items()}

    weeks_data = {w: get(f"https://api.sleeper.app/v1/league/{league_id}/matchups/{w}") for w in weeks}
    return league, roster_to_team, weeks, weeks_data

def compute_core(roster_to_team: Dict[int,str], weeks: List[int], weeks_data: Dict[int, list]):
    pf, pa, w, l, gp = {}, {}, {}, {}, {}
    weekly_games, weekly_scores = {}, {}

    def add_team(t):
        pf.setdefault(t, 0.0); pa.setdefault(t, 0.0)
        w.setdefault(t, 0); l.setdefault(t, 0); gp.setdefault(t, 0)

    for week in weeks:
        g = weeks_data[week]
        weekly_games[week] = []
        weekly_scores[week] = {}

        # collect all scores (for All-Play)
        for m in g:
            t = roster_to_team.get(m["roster_id"], f"Roster {m['roster_id']}")
            weekly_scores[week][t] = float(m["points"])

        # pair by matchup
        by_mid = {}
        for m in g:
            by_mid.setdefault(m["matchup_id"], []).append(m)

        for mid, pair in by_mid.items():
            if len(pair) != 2:
                continue
            a, b = pair
            ta = roster_to_team.get(a["roster_id"], f"Roster {a['roster_id']}")
            tb = roster_to_team.get(b["roster_id"], f"Roster {b['roster_id']}")
            sa, sb = float(a["points"]), float(b["points"])
            for t in (ta, tb): add_team(t)

            pf[ta] += sa; pf[tb] += sb
            pa[ta] += sb; pa[tb] += sa
            gp[ta] += 1; gp[tb] += 1

            winner = ta if sa > sb else tb
            if winner == ta:
                w[ta] += 1; l[tb] += 1
            else:
                w[tb] += 1; l[ta] += 1

            weekly_games[week].append((ta, sa, tb, sb, abs(sa - sb), winner))

    return pf, pa, w, l, gp, weekly_games, weekly_scores

def compute_all_play(weekly_scores: Dict[int, Dict[str, float]]) -> Dict[str, float]:
    ap_wins, ap_weeks = {}, {}
    for scores in weekly_scores.values():
        teams = list(scores.keys()); n = len(teams)
        if n <= 1: continue
        for t in teams:
            s = scores[t]
            wins_vs = sum(1 for other in teams if other != t and s > scores[other])
            frac = wins_vs / (n - 1)
            ap_wins[t]  = ap_wins.get(t, 0) + frac
            ap_weeks[t] = ap_weeks.get(t, 0) + 1
    return {t: ap_wins[t] / ap_weeks[t] for t in ap_wins}

def build_standings(pf, pa, w, l):
    rows = [[t, w.get(t,0), l.get(t,0),
             round(pf[t],2), round(pa[t],2), round(pf[t]-pa[t],2)]
            for t in pf.keys()]
    # Sort by PF (index 3) descending
    rows.sort(key=lambda r: r[3], reverse=True)
    return rows

from reportlab.platypus import Paragraph  # already imported earlier

def build_sos_luck_rows(pf, pa, w, l, gp, allplay_pct):
    rows = []
    sos_vals = []
    for t in pf.keys():
        games = gp.get(t, 0) or 1
        sos   = pa.get(t, 0.0) / games
        ap    = allplay_pct.get(t, 0.0)
        exp_w = ap * games
        luck  = w.get(t, 0) - exp_w
        sos_vals.append(sos)

        # Luck badge (Paragraph so ReportLab renders it)
        if luck > 0.5:
            badge_html = f"<font color='green'>🍀 {luck:.2f}</font>"
        elif luck < -0.5:
            badge_html = f"<font color='red'>😬 {luck:.2f}</font>"
        else:
            badge_html = f"<font color='gray'>⚖️ {luck:.2f}</font>"
        badge = Paragraph(badge_html, getSampleStyleSheet()["BodyText"])

        rows.append([t, w.get(t,0), l.get(t,0),
                     round(pf[t],2), round(pa[t],2),
                     round(sos,2), round(ap,3), round(exp_w,2), badge])

    avg_sos = mean(sos_vals) if sos_vals else 0.0
    avg_row = ["League Avg","","","-","-", round(avg_sos,2), "-", "-", "-"]

    # Sort non-average rows by PF (index 3) descending, then append League Avg
    rows.sort(key=lambda r: r[3], reverse=True)
    rows.append(avg_row)
    return rows


def derive_maps(standings, sos_luck_rows):
    teams     = [r[0] for r in standings]
    wins_map  = {r[0]: r[1] for r in standings}
    loss_map  = {r[0]: r[2] for r in standings}
    sos_map   = {r[0]: next(x[5] for x in sos_luck_rows if x[0]==r[0]) if r[0]!="League Avg" else None
                 for r in standings}
    luck_map  = {}
    for r in sos_luck_rows:
        t = r[0]
        if t == "League Avg": continue
        m = re.search(r"([+-]?\d+\.\d+|[+-]?\d+)", str(r[8]))
        luck_map[t] = float(m.group(1)) if m else 0.0
    return teams, wins_map, loss_map, sos_map, luck_map

# ---- Banter pools (5 lines each) ----
PRAISES_TOP = [
    "Bow down, peasants. The juggernaut marches on.",
    "Not even Thanos could snap this streak away.",
    "The only thing scarier than this record is their waiver wire luck.",
    "Three weeks in and already acting like they own the trophy case.",
    "If dominance was a crime, you’d be serving life without parole."
]
ROASTS_DOORMAT = [
    "At this point, you’re basically a bye week.",
    "Even AI auto-draft teams feel sorry for you.",
    "Your opponents don’t prepare anymore — they just stretch.",
    "The only streak you’re building is a losing one.",
    "ESPN just moved your highlight reel to the blooper section."
]
ROASTS_LUCKY = [
    "Frauds, the lot of you. Living on borrowed touchdowns.",
    "This record is faker than a $3 Rolex at a flea market.",
    "Winning matchups the same way toddlers win arguments: volume, not logic.",
    "The Fantasy Gods are carrying you like a drunk friend at 2am.",
    "You’ve got more plot armor than a main character in a Marvel movie."
]
ROASTS_UNLUCKY = [
    "Someone angered the fantasy gods. Try a blood sacrifice.",
    "You’d beat half the league every week… too bad you always play the wrong half.",
    "Your team’s motto should be: ‘So close, yet so useless.’",
    "This isn’t bad luck anymore — it’s a personal vendetta.",
    "You’re basically the NFL version of Murphy’s Law."
]
ROASTS_EASIEST = [
    "Congrats on your cupcake diet. Enjoy those empty calories.",
    "Facing this schedule is like speedrunning Easy Mode.",
    "Your toughest opponent so far has been bye weeks.",
    "Padding your stats against charity cases, I see.",
    "This isn’t a schedule — it’s a fantasy daycare."
]
ROASTS_HARDEST = [
    "Forget fantasy football — you’ve been dropped into The Hunger Games.",
    "You’re not playing matchups, you’re facing war crimes.",
    "Your schedule is so brutal, even Dark Souls looks easy.",
    "Every week’s a gauntlet, and you’re the practice dummy.",
    "This isn’t SOS, it’s SOS — as in, send help."
]

def pick(lines: List[str]) -> str:
    return random.choice(lines)

def picks_and_pans(weekly_games):
    closest, blowouts = [], []
    for week, games in weekly_games.items():
        if not games: continue
        cg = min(games, key=lambda g: g[4])
        bg = max(games, key=lambda g: g[4])
        closest.append((week, cg)); blowouts.append((week, bg))
    return closest, blowouts

def write_pdf(filename: str, league_name: str, weeks: List[int],
              standings, sos_luck_rows, closest, blowouts,
              undefeated, winless, luckiest, unluckiest, easiest, hardest, sos_map, luck_map):
    doc = SimpleDocTemplate(filename, pagesize=letter)
    styles = getSampleStyleSheet()
    H1 = ParagraphStyle("H1", parent=styles["Heading1"], alignment=1)
    story = []

    story.append(Paragraph(f"🏈 {league_name} — Weeks {min(weeks)}–{max(weeks)} Recap 🏈", H1))
    story.append(Spacer(1, 8))

    # Banter sections (Ron Burgundy voice)
    if undefeated:
        story.append(Paragraph("Top Dogs (Undefeated)", styles["Heading2"]))
        story.append(Paragraph(", ".join(undefeated), styles["Normal"]))
        story.append(Paragraph(pick(PRAISES_TOP), styles["Italic"]))
        story.append(Spacer(1, 8))

    if winless:
        story.append(Paragraph("League Doormats (Winless)", styles["Heading2"]))
        story.append(Paragraph(", ".join(winless), styles["Normal"]))
        story.append(Paragraph(pick(ROASTS_DOORMAT), styles["Italic"]))
        story.append(Spacer(1, 8))

    if luckiest:
        story.append(Paragraph("Luckiest Teams (by All-Play vs Record)", styles["Heading2"]))
        story.append(Paragraph(", ".join([f"{t} ({luck_map[t]:+.2f})" for t in luckiest]), styles["Normal"]))
        story.append(Paragraph(pick(ROASTS_LUCKY), styles["Italic"]))
        story.append(Spacer(1, 8))

    if unluckiest:
        story.append(Paragraph("Unluckiest Teams (by All-Play vs Record)", styles["Heading2"]))
        story.append(Paragraph(", ".join([f"{t} ({luck_map[t]:+.2f})" for t in unluckiest]), styles["Normal"]))
        story.append(Paragraph(pick(ROASTS_UNLUCKY), styles["Italic"]))
        story.append(Spacer(1, 8))

    if easiest:
        story.append(Paragraph("Easiest Schedule (Lowest SOS)", styles["Heading2"]))
        story.append(Paragraph(", ".join([f"{t} (SOS {sos_map[t]:.2f})" for t in easiest]), styles["Normal"]))
        story.append(Paragraph(pick(ROASTS_EASIEST), styles["Italic"]))
        story.append(Spacer(1, 8))

    if hardest:
        story.append(Paragraph("Hardest Schedule (Highest SOS)", styles["Heading2"]))
        story.append(Paragraph(", ".join([f"{t} (SOS {sos_map[t]:.2f})" for t in hardest]), styles["Normal"]))
        story.append(Paragraph(pick(ROASTS_HARDEST), styles["Italic"]))
        story.append(Spacer(1, 12))

    # Standings
    story.append(Paragraph("Standings", styles["Heading2"]))
    std_data = [["Team","W","L","PF","PA","Diff"]] + standings
    std_table = Table(std_data, hAlign="CENTER", colWidths=[180,35,35,70,70,60])
    std_table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.darkblue),
        ("TEXTCOLOR",(0,0),(-1,0),colors.whitesmoke),
        ("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("GRID",(0,0),(-1,-1),0.25,colors.grey)
    ]))
    story.append(std_table)
    story.append(Spacer(1, 10))

    # SOS & Luck
    story.append(Paragraph("Strength of Schedule & Luck", styles["Heading2"]))
    sos_hdr=["Team","W","L","PF","PA","SOS (OppAvg)","All-Play%","Exp W","Luck"]
    sos_table = Table([sos_hdr] + sos_luck_rows, hAlign="CENTER",
                      colWidths=[160,30,30,60,60,70,60,50,60])
    sos_table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.darkgreen),
        ("TEXTCOLOR",(0,0),(-1,0),colors.whitesmoke),
        ("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("GRID",(0,0),(-1,-1),0.25,colors.grey)
    ]))
    story.append(sos_table)
    story.append(Spacer(1, 10))

    # Heart-Attack & Blowouts
    story.append(Paragraph("Heart-Attack Matchups", styles["Heading2"]))
    for week,(ta,sa,tb,sb,margin,_) in closest:
        story.append(Paragraph(f"Week {week}: {ta} {sa:.2f} d. {tb} {sb:.2f} (margin {margin:.2f})", styles["Normal"]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Blowouts of the Week", styles["Heading2"]))
    for week,(ta,sa,tb,sb,margin,winner) in blowouts:
        if winner == tb: ta,tb,sa,sb = tb,ta,sb,sa
        story.append(Paragraph(f"Week {week}: {ta} {sa:.2f} over {tb} {sb:.2f} (margin {abs(sa-sb):.2f})", styles["Normal"]))

    story.append(Spacer(1, 10))
    story.append(Paragraph("Commissioner’s Closing Words", styles["Heading2"]))
    story.append(Paragraph(
        "Undefeateds, keep strutting. Winless, keep praying. Middle pack, every start/sit could swing your season. Stay classy.",
        styles["Normal"]
    ))

    doc.build(story)

def main():
    league, roster_to_team, weeks, weeks_data = load_data(LEAGUE_ID)
    pf, pa, w, l, gp, weekly_games, weekly_scores = compute_core(roster_to_team, weeks, weeks_data)
    standings      = build_standings(pf, pa, w, l)
    allplay_pct    = compute_all_play(weekly_scores)
    sos_luck_rows  = build_sos_luck_rows(pf, pa, w, l, gp, allplay_pct)
    teams, wins_map, loss_map, sos_map, luck_map = derive_maps(standings, sos_luck_rows)

    # categories
    undefeated = [t for t in teams if wins_map[t] > 0 and loss_map[t] == 0]
    winless    = [t for t in teams if wins_map[t] == 0 and loss_map[t] > 0]
    luckiest   = sorted(luck_map.keys(), key=lambda x: luck_map[x], reverse=True)[:3]
    unluckiest = sorted(luck_map.keys(), key=lambda x: luck_map[x])[:3]

    non_avg   = [r[0] for r in sos_luck_rows if r[0] != "League Avg"]
    easiest   = sorted(non_avg, key=lambda t: sos_map[t])[:2]
    hardest   = sorted(non_avg, key=lambda t: sos_map[t], reverse=True)[:2]

    closest, blowouts = picks_and_pans(weekly_games)

    out = f"{TITLE_PREFIX}_Weeks{min(weeks)}_{max(weeks)}_Recap.pdf"
    write_pdf(
        out, league.get("name", TITLE_PREFIX), weeks,
        standings, sos_luck_rows, closest, blowouts,
        undefeated, winless, luckiest, unluckiest, easiest, hardest, sos_map, luck_map
    )
    print(f"Generated: {out}")

if __name__ == "__main__":
    main()


