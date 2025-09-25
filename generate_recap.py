# generate_recap.py
# NZFFFA weekly recap PDF generator (Sleeper -> PDF)
# Requires: requests, reportlab
#   pip install requests reportlab

import requests, math
from datetime import datetime
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors

LEAGUE_ID = "1180242943416172544"   # <-- your league
TITLE = "NZFFFA Championship — Weekly Recap"

def get(url):
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    return r.json()

def load_data(league_id: str):
    league = get(f"https://api.sleeper.app/v1/league/{league_id}")
    users = get(f"https://api.sleeper.app/v1/league/{league_id}/users")
    rosters = get(f"https://api.sleeper.app/v1/league/{league_id}/rosters")

    # Which week(s) should we include?
    # Use last_scored_leg (last completed scoring week)
    last_scored = int(league.get("settings", {}).get("last_scored_leg", 1))
    start_week = int(league.get("settings", {}).get("start_week", 1))
    weeks = list(range(start_week, last_scored + 1))

    # Build quick maps
    owner_to_team = {}
    for u in users:
        tn = (u.get("metadata", {}) or {}).get("team_name")
        owner_to_team[u["user_id"]] = (tn.strip() if tn else u["display_name"].strip())

    roster_to_owner = {r["roster_id"]: r["owner_id"] for r in rosters}
    roster_to_team = {rid: owner_to_team.get(oid, f"Roster {rid}") for rid, oid in roster_to_owner.items()}

    # Pull all matchups
    weeks_data = {}
    for w in weeks:
        weeks_data[w] = get(f"https://api.sleeper.app/v1/league/{league_id}/matchups/{w}")

    return league, roster_to_team, weeks, weeks_data

def compute_tables(roster_to_team, weeks, weeks_data):
    # PF/PA/W/L
    pf, pa, w, l = {}, {}, {}, {}
    def add_team(t):
        pf.setdefault(t, 0.0); pa.setdefault(t, 0.0); w.setdefault(t, 0); l.setdefault(t, 0)

    # also keep per-week results for highlights
    weekly_results = {}  # week -> list of (teamA, scoreA, teamB, scoreB, margin, winner)
    for week in weeks:
        g = weeks_data[week]
        # group by matchup_id
        by_mid = {}
        for m in g:
            by_mid.setdefault(m["matchup_id"], []).append(m)
        weekly_results[week] = []
        for mid, pair in by_mid.items():
            if len(pair) != 2:  # ignore odd cases/byes
                continue
            a, b = pair[0], pair[1]
            ta = roster_to_team.get(a["roster_id"], f"Roster {a['roster_id']}")
            tb = roster_to_team.get(b["roster_id"], f"Roster {b['roster_id']}")
            sa = float(a["points"]); sb = float(b["points"])
            for t in (ta, tb): add_team(t)
            pf[ta] += sa; pf[tb] += sb
            pa[ta] += sb; pa[tb] += sa
            if sa > sb:
                w[ta] += 1; l[tb] += 1; winner = ta
            else:
                w[tb] += 1; l[ta] += 1; winner = tb
            margin = abs(sa - sb)
            weekly_results[week].append((ta, sa, tb, sb, margin, winner))
    return pf, pa, w, l, weekly_results

def standings_rows(pf, pa, w, l):
    rows=[]
    for t in pf.keys():
        rows.append([t, w.get(t,0), l.get(t,0), round(pf[t],2), round(pa[t],2), round(pf[t]-pa[t],2)])
    # sort by Wins, Diff, PF
    rows.sort(key=lambda r:(r[1], r[5], r[3]), reverse=True)
    return rows

def picks_and_pans(weekly_results):
    # Closest & Blowout per week
    closest, blowouts = [], []
    for week, games in weekly_results.items():
        if not games: continue
        cg = min(games, key=lambda g: g[4])
        bg = max(games, key=lambda g: g[4])
        closest.append((week, cg))
        blowouts.append((week, bg))
    return closest, blowouts

def write_pdf(filename, league_name, weeks, standings, closest, blowouts):
    doc = SimpleDocTemplate(filename, pagesize=letter)
    styles = getSampleStyleSheet()
    H1 = ParagraphStyle("H1", parent=styles["Heading1"], alignment=1)
    story = []
    story.append(Paragraph(f"🏈 {league_name} — Weeks {min(weeks)}–{max(weeks)} Recap 🏈", H1))
    story.append(Spacer(1, 8))

    # Standings
    story.append(Paragraph("Standings After Week " + str(max(weeks)), styles["Heading2"]))
    data = [["Team","W","L","PF","PA","Diff"]] + standings
    table = Table(data, hAlign="CENTER", colWidths=[190,40,40,70,70,60])
    table.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,0),colors.darkblue),
        ("TEXTCOLOR",(0,0),(-1,0),colors.whitesmoke),
        ("ALIGN",(0,0),(-1,-1),"CENTER"),
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("GRID",(0,0),(-1,-1),0.25,colors.grey)
    ]))
    story.append(table)
    story.append(Spacer(1, 12))

    # Top Dogs / Doormats from standings
    wins_map = {r[0]: r[1] for r in standings}
    losses_map = {r[0]: r[2] for r in standings}
    top_dogs = [t for t in wins_map if wins_map[t]==max(wins_map.values())]
    doormats = [t for t in losses_map if losses_map[t]==max(losses_map.values())]

    story.append(Paragraph("Top Dogs", styles["Heading2"]))
    story.append(Paragraph(", ".join(top_dogs) if top_dogs else "—", styles["Normal"]))
    story.append(Spacer(1, 8))

    story.append(Paragraph("League Doormats", styles["Heading2"]))
    story.append(Paragraph(", ".join(doormats) if doormats else "—", styles["Normal"]))
    story.append(Spacer(1, 8))

    # Heart-Attack (closest)
    story.append(Paragraph("Heart-Attack Matchups", styles["Heading2"]))
    for week, (ta, sa, tb, sb, margin, _) in closest:
        story.append(Paragraph(
            f"Week {week}: {ta} {sa:.2f} d. {tb} {sb:.2f} (margin {margin:.2f})",
            styles["Normal"]
        ))
    story.append(Spacer(1, 8))

    # Blowouts
    story.append(Paragraph("Blowouts of the Week", styles["Heading2"]))
    for week, (ta, sa, tb, sb, margin, winner) in blowouts:
        # winner might be ta or tb; show winner first
        if winner == tb: ta, tb, sa, sb = tb, ta, sb, sa
        story.append(Paragraph(
            f"Week {week}: {ta} {sa:.2f} over {tb} {sb:.2f} (margin {abs(sa-sb):.2f})",
            styles["Normal"]
        ))
    story.append(Spacer(1, 10))

    # Burgundy sign-off
    story.append(Paragraph("Commissioner’s Closing Words", styles["Heading2"]))
    story.append(Paragraph(
        "Undefeateds, keep strutting. Winless, keep praying. Middle pack, every start/sit could swing your season. Stay classy.",
        styles["Normal"]
    ))

    doc.build(story)

def main():
    league, roster_to_team, weeks, weeks_data = load_data(LEAGUE_ID)
    pf, pa, w, l, weekly_results = compute_tables(roster_to_team, weeks, weeks_data)
    st_rows = standings_rows(pf, pa, w, l)
    closest, blowouts = picks_and_pans(weekly_results)

    week_range = f"Weeks{min(weeks)}_{max(weeks)}"
    out = f"NZFFFA_{week_range}_Recap.pdf"
    write_pdf(out, league.get("name","NZFFFA"), weeks, st_rows, closest, blowouts)
    print(f"Generated: {out}")

if __name__ == "__main__":
    main()
