"""Ask Claude (with web search) to research each league and write the reports.

Optional: only runs when the ANTHROPIC_API_KEY secret is set. Without it, the site still
updates league data, and reports can be written in Claude chat and uploaded as
data/<key>/reports.json.

Runs when MODE=full, or MODE=check and fetch_league.py flagged relevant changes.
"""
import json
import os
from datetime import datetime, timezone, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
CONFIG = json.load(open(os.path.join(ROOT, "config.json")))

SECTIONS = ["brief", "startsit", "waivers", "trades", "matchup"]


def should_run(mode, changes, reports):
    if mode == "full":
        return True
    if not changes.get("relevant"):
        return False
    last = reports.get("generated_at") if reports else None
    if last:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(last)
        if age < timedelta(hours=CONFIG.get("min_hours_between_research", 3)):
            return False
    return True


def fmt_roster(team, detail=True):
    rows = []
    for p in team["roster"]:
        line = f"- {p.get('slot', '?'):>4} | {p['name']} ({p['pos']}, {p['team']}) | {p['injury']} | proj {p['proj']} | last wk {p['last_week']}"
        if detail and p.get("outlook"):
            line += f" | ESPN note: {p['outlook'][:300]}"
        rows.append(line)
    return "\n".join(rows)


def build_prompt(league, changes):
    teams = {t["id"]: t for t in league["teams"]}
    me = teams[league["my_team_id"]]
    opp = teams.get(league.get("opponent_id"))
    others = [t for t in league["teams"] if t["id"] != me["id"]]
    standings = sorted(league["teams"], key=lambda t: (-t["wins"], -t["points_for"]))
    s = league["settings"]
    fa = "\n".join(
        f"- {p['name']} ({p['pos']}, {p['team']}) | {p['injury']} | owned {p['owned_pct']}% ({p['owned_change']:+}) "
        f"| proj {p['proj']}"
        for p in league["free_agents"][:80]
    )
    league_rosters = "\n\n".join(
        f"{t['name']} ({t['wins']}-{t['losses']}, {t['points_for']} PF):\n{fmt_roster(t, detail=False)}" for t in others
    )
    return f"""You are the fantasy football coach for "{me['name']}" in the league "{league['league_name']}".
Today is {datetime.now(timezone.utc).strftime('%A, %B %d, %Y')} (UTC). It is NFL week {league['week']}.

LEAGUE RULES: {s['teams']} teams, {s['scoring']} scoring, FAAB budget ${s['faab_budget']} (this team has spent ${me['faab_spent']}),
{s['playoff_teams']} playoff teams, seeded by record (tiebreaker: {s.get('playoff_tiebreaker')}) over {s['regular_season_weeks']} regular-season weeks.
Trade deadline (epoch ms): {s.get('trade_deadline')}; veto votes needed: {s.get('trade_veto_votes')}.
Wins decide playoff seeding; total points only break ties, so treat each matchup as must-win when the record is poor.

STANDINGS:
{chr(10).join(f"- {t['name']}: {t['wins']}-{t['losses']}, {t['points_for']} PF" for t in standings)}

MY ROSTER (slot | player | status | ESPN projection | last week | ESPN note):
{fmt_roster(me)}

THIS WEEK'S OPPONENT: {opp['name'] if opp else 'unknown'} | ESPN projection me {league['projection']['me']} vs them {league['projection']['opponent']}, my win probability {league['projection']['my_win_prob']}

EVERY OTHER TEAM'S ROSTER (for trades and opponent analysis):
{league_rosters}

TOP AVAILABLE PLAYERS (free agents/waivers, sorted by ownership):
{fa}

RECENT LEAGUE CHANGES: {'; '.join(changes.get('events', [])) or 'none'}
RECENT TRANSACTIONS: {json.dumps(league['transactions'][:25])}

INSTRUCTIONS
Use web search to verify current information before recommending: official injury and practice reports,
beat-writer news, depth-chart and snap/target trends, Vegas spreads and totals, and weather for outdoor games.
Search specifically for every player on my roster with an injury designation, for any start/sit decision that is close,
for the top waiver candidates, and for each player you propose trading for or away.
Prefer primary sources (team sites, NFL.com injury reports, established beat reporters).
Never invent statuses or numbers; if something can't be verified, say so. Give confidence (High/Medium/Low) on each call.

Write five sections in markdown, each starting with its marker on its own line exactly as shown:
<<<brief>>>
Research brief: key news for my players and my opponent's players, each item with its source name.
<<<startsit>>>
Optimal lineup table (Slot | Start | Confidence | Why), close calls, and what to re-check before kickoff (with times).
<<<waivers>>>
Ranked adds from the available list only (Player | Why | FAAB bid in $ | Drop), plus players to hold.
<<<trades>>>
My roster's surpluses and needs. Then 3-4 specific, realistic trade offers with a named partner team from the rosters above:
table (Partner | I give | I get | Why it works for me | Why they'd accept | Confidence). Base "why they'd accept" on the
partner's actual roster needs and record. Add buy-low and sell-high candidates, and note if a trade strengthens a
direct rival for points or a playoff seed.
<<<matchup>>>
Projected score for both teams, win probability, the swing players, and one move that raises my odds.
Be concise and decisive."""


def call_claude(prompt):
    import anthropic
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": prompt}]
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": CONFIG.get("max_web_searches", 15)}]
    text, sources = [], {}
    for _ in range(4):  # server-side search can pause long turns; continue up to a few times
        resp = client.messages.create(model=CONFIG["model"], max_tokens=10000, tools=tools, messages=messages)
        for block in resp.content:
            if block.type == "text":
                text.append(block.text)
            elif block.type == "web_search_tool_result" and isinstance(block.content, list):
                for r in block.content:
                    url = getattr(r, "url", None)
                    if url:
                        sources[url] = getattr(r, "title", url)
        if resp.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": resp.content}]
    return "".join(text), [{"title": t, "url": u} for u, t in sources.items()]


def split_sections(text):
    out = {k: "" for k in SECTIONS}
    current = None
    for line in text.splitlines():
        marker = line.strip()
        if marker.startswith("<<<") and marker.endswith(">>>") and marker[3:-3] in out:
            current = marker[3:-3]
            continue
        if current:
            out[current] += line + "\n"
    if not any(out.values()):
        out["brief"] = text  # fall back to showing everything
    return {k: v.strip() for k, v in out.items()}


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("No ANTHROPIC_API_KEY secret; skipping automatic research (league data is still saved).")
        return
    mode = os.environ.get("MODE", "check")
    for lg in CONFIG.get("leagues", []):
        if lg.get("platform") == "manual":
            continue  # researched in Claude chat
        folder = os.path.join(DATA, lg["key"])
        lpath = os.path.join(folder, "league.json")
        if not os.path.exists(lpath):
            print(f"[{lg['key']}] no league data yet; skipping.")
            continue
        league = json.load(open(lpath))
        changes = json.load(open(os.path.join(folder, "changes.json")))
        rpath = os.path.join(folder, "reports.json")
        reports = json.load(open(rpath)) if os.path.exists(rpath) else {}
        if not should_run(mode, changes, reports):
            print(f"[{lg['key']}] nothing new; skipping research.")
            continue
        try:
            text, sources = call_claude(build_prompt(league, changes))
        except Exception as e:
            print(f"[{lg['key']}] research failed: {e}")
            continue
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        result = {"generated_at": now, "week": league["week"], "trigger": mode,
                  "changes": changes.get("events", []), "sources": sources, **split_sections(text)}
        json.dump(result, open(rpath, "w"), indent=1)
        os.makedirs(os.path.join(folder, "history"), exist_ok=True)
        json.dump(result, open(os.path.join(folder, "history", now.replace(":", "-") + ".json"), "w"), indent=1)
        print(f"[{lg['key']}] reports written ({len(sources)} sources).")


if __name__ == "__main__":
    main()
