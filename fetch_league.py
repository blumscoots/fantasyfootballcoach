"""Pull the league from ESPN's public data feed, slim it down, and record what changed.

Writes:
  data/league.json   compact snapshot (settings, standings, rosters, matchup, free agents)
  data/changes.json  what changed since the previous snapshot, plus whether research should rerun
"""
import json
import os
import sys
from datetime import datetime, timezone

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CONFIG = json.load(open(os.path.join(ROOT, "config.json")))

BASE = ("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/"
        f"{CONFIG['season']}/segments/0/leagues/{CONFIG['league_id']}")
HEADERS = {"User-Agent": "fantasy-coach/1.0 (personal league tool)"}

POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
SLOTS = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "D/ST", 17: "K", 20: "BN", 21: "IR", 23: "FLEX"}
PRO_TEAMS = {
    0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET",
    9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR", 15: "MIA", 16: "MIN",
    17: "NE", 18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC",
    25: "SF", 26: "SEA", 27: "TB", 28: "WSH", 29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU",
}


def get(views, extra=None, filter_header=None):
    params = [("view", v) for v in views] + list((extra or {}).items())
    headers = dict(HEADERS)
    if filter_header:
        headers["X-Fantasy-Filter"] = json.dumps(filter_header)
    r = requests.get(BASE, params=params, headers=headers, timeout=60)
    r.raise_for_status()
    return r.json()


def stat_total(player, week, source):
    """source 1 = ESPN projection, 0 = actual points."""
    for s in player.get("stats", []):
        if s.get("scoringPeriodId") == week and s.get("statSourceId") == source and s.get("statSplitTypeId") == 1:
            return round(s.get("appliedTotal", 0.0), 1)
    return None


def slim_player(player, week, slot_id=None):
    out = {
        "id": player["id"],
        "name": player.get("fullName"),
        "pos": POSITIONS.get(player.get("defaultPositionId"), "?"),
        "team": PRO_TEAMS.get(player.get("proTeamId"), "?"),
        "injury": player.get("injuryStatus", "ACTIVE"),
        "proj": stat_total(player, week, 1),
        "last_week": stat_total(player, week - 1, 0) if week > 1 else None,
        "owned_pct": round(player.get("ownership", {}).get("percentOwned", 0), 1),
        "owned_change": round(player.get("ownership", {}).get("percentChange", 0), 2),
        "outlook": (player.get("outlooks", {}).get("outlooksByWeek", {}) or {}).get(str(week), ""),
    }
    if slot_id is not None:
        out["slot"] = SLOTS.get(slot_id, str(slot_id))
    return out


def main():
    core = get(["mSettings", "mTeam", "mStatus"])
    week = core["status"]["currentMatchupPeriod"]
    settings = core["settings"]

    rosters = get(["mRoster"], {"scoringPeriodId": week})
    matchups = get(["mMatchupScore"], {"scoringPeriodId": week})
    tx = get(["mTransactions2"], {"scoringPeriodId": week})
    fa = get(["kona_player_info"], {"scoringPeriodId": week}, filter_header={
        "players": {
            "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
            "filterSlotIds": {"value": [0, 2, 4, 6, 16, 17, 23]},
            "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
            "limit": CONFIG["free_agent_limit"],
        }
    })

    members = {m["id"]: f"{m.get('firstName', '')} {m.get('lastName', '')}".strip() for m in core.get("members", [])}
    teams = {}
    for t in core["teams"]:
        rec = t["record"]["overall"]
        teams[t["id"]] = {
            "id": t["id"],
            "name": t.get("name", t.get("abbrev")),
            "owner": members.get(t.get("primaryOwner"), ""),
            "wins": rec["wins"], "losses": rec["losses"], "ties": rec["ties"],
            "points_for": round(rec["pointsFor"], 2), "points_against": round(rec["pointsAgainst"], 2),
            "faab_spent": t.get("transactionCounter", {}).get("acquisitionBudgetSpent", 0),
            "waiver_rank": t.get("waiverRank"),
            "roster": [],
        }

    id_to_name = {}
    for t in rosters["teams"]:
        for e in t.get("roster", {}).get("entries", []):
            p = e["playerPoolEntry"]["player"]
            teams[t["id"]]["roster"].append(slim_player(p, week, e.get("lineupSlotId")))
            id_to_name[p["id"]] = p.get("fullName")

    free_agents = []
    for entry in fa.get("players", []):
        p = entry["player"]
        free_agents.append(slim_player(p, week))
        id_to_name[p["id"]] = p.get("fullName")

    my_id = CONFIG["team_id"]
    opponent_id, my_proj, opp_proj, my_win_prob = None, None, None, None
    for m in matchups.get("schedule", []):
        if m.get("matchupPeriodId") != week:
            continue
        home, away = m.get("home", {}), m.get("away", {})
        for me, them in ((home, away), (away, home)):
            if me.get("teamId") == my_id:
                opponent_id = them.get("teamId")
                my_proj = round(me.get("totalProjectedPoints", 0) or 0, 1)
                opp_proj = round(them.get("totalProjectedPoints", 0) or 0, 1)
                my_win_prob = me.get("winProbability")

    transactions = []
    for t in tx.get("transactions", []):
        if t.get("status") != "EXECUTED" or t.get("type") == "ROSTER":
            continue  # skip plain lineup shuffles
        items = [{
            "type": i.get("type"),
            "player": id_to_name.get(i.get("playerId"), str(i.get("playerId"))),
            "from_team": teams.get(i.get("fromTeamId"), {}).get("name"),
            "to_team": teams.get(i.get("toTeamId"), {}).get("name"),
        } for i in t.get("items", [])]
        transactions.append({
            "id": t["id"], "type": t.get("type"), "team": teams.get(t.get("teamId"), {}).get("name"),
            "bid": t.get("bidAmount", 0), "date": t.get("processDate") or t.get("proposedDate"), "items": items,
        })

    acq = settings.get("acquisitionSettings", {})
    sched = settings.get("scheduleSettings", {})
    league = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "league_name": settings.get("name"),
        "week": week,
        "settings": {
            "teams": settings.get("size"),
            "scoring": settings.get("scoringSettings", {}).get("playerRankType"),
            "faab_budget": acq.get("acquisitionBudget"),
            "playoff_teams": sched.get("playoffTeamCount"),
            "playoff_seeding": sched.get("playoffSeedingRule"),
            "regular_season_weeks": sched.get("matchupPeriodCount"),
        },
        "my_team_id": my_id,
        "opponent_id": opponent_id,
        "projection": {"me": my_proj, "opponent": opp_proj, "my_win_prob": my_win_prob},
        "teams": list(teams.values()),
        "free_agents": free_agents,
        "transactions": transactions,
    }

    path = os.path.join(DATA, "league.json")
    previous = json.load(open(path)) if os.path.exists(path) else None
    changes = diff(previous, league)
    os.makedirs(DATA, exist_ok=True)
    json.dump(league, open(path, "w"), indent=1)
    json.dump(changes, open(os.path.join(DATA, "changes.json"), "w"), indent=1)
    print(f"Week {week}: {len(changes['events'])} change(s); relevant={changes['relevant']}")


def diff(prev, cur):
    events, relevant = [], False
    if prev is None:
        return {"at": cur["fetched_at"], "events": ["First snapshot"], "relevant": True}
    if prev.get("week") != cur["week"]:
        events.append(f"New week: {cur['week']}")
        relevant = True

    watch = {cur["my_team_id"], cur.get("opponent_id")}
    prev_teams = {t["id"]: t for t in prev.get("teams", [])}
    for t in cur["teams"]:
        before = {p["id"]: p for p in prev_teams.get(t["id"], {}).get("roster", [])}
        after = {p["id"]: p for p in t["roster"]}
        for pid in after.keys() - before.keys():
            events.append(f"{t['name']} added {after[pid]['name']}")
            relevant = True
        for pid in before.keys() - after.keys():
            events.append(f"{t['name']} dropped {before[pid]['name']}")
            relevant = True
        if t["id"] in watch:
            for pid in after.keys() & before.keys():
                if after[pid]["injury"] != before[pid]["injury"]:
                    events.append(f"{after[pid]['name']} ({t['name']}): {before[pid]['injury']} -> {after[pid]['injury']}")
                    relevant = True
    if prev.get("opponent_id") != cur.get("opponent_id"):
        relevant = True
    return {"at": cur["fetched_at"], "events": events, "relevant": relevant}


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"ESPN request failed: {e}", file=sys.stderr)
        sys.exit(1)
