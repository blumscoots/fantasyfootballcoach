# Fantasy Coach

An automatic fantasy football coach for the ESPN league **Premier League** (ID 26014871), team **Mr. Chow**.

A scheduled GitHub Action pulls the league from ESPN, checks what changed, has Claude research the week
with web search, and publishes the results to a GitHub Pages site.

## How it works

| Step | File | What it does |
|---|---|---|
| 1 | `scripts/fetch_league.py` | Reads settings, standings, every roster, this week's matchup, transactions and the top 150 free agents from ESPN. Saves `data/league.json` and records changes in `data/changes.json`. |
| 2 | `scripts/research.py` | Sends your roster, opponent, free agents and recent changes to Claude with web search. Writes the research brief, start/sit, waivers and matchup reports to `data/reports.json` (older runs go to `data/history/`). |
| 3 | `.github/workflows/update.yml` | Runs steps 1 and 2 on a schedule and commits the results. |
| 4 | `index.html` | The website. Reads the two JSON files and shows your lineup, the reports and the league. |

### Schedule (times in Pacific)

- **Tuesday 9am:** full research (waivers).
- **Wednesday, Thursday, Friday 6pm:** full research (practice and injury reports).
- **Sunday 8am:** full research (final start/sit).
- **Every 4 hours:** league check only. Research reruns only if a roster changed, a player on your team or your opponent's team changed injury status, or a new week started, and not more than once every 3 hours.

GitHub can delay scheduled runs by several minutes to over an hour, so don't rely on it for last-minute inactives.

## Setup (about 15 minutes)

1. **Create the repo.** On GitHub, click **New repository**, name it (for example `fantasy-coach`), set it to **Public**, and create it.
   Upload every file from this folder, including the hidden `.github` folder and `.nojekyll`.
   (Easiest: drag the unzipped folder's contents into the "uploading an existing file" page, or use GitHub Desktop.)
2. **Optional: get an Anthropic API key** for automatic research. Without one, the site still updates the league data on schedule, and you can get research by asking Claude in chat and uploading the `reports.json` it gives you into the `data` folder.
   To automate it instead: Go to console.anthropic.com, add billing, and create a key.
   API usage is billed separately from a Claude.ai subscription.
3. **Add the key as a secret.** In the repo: **Settings → Secrets and variables → Actions → New repository secret**.
   Name it `ANTHROPIC_API_KEY` and paste the key.
4. **Let the workflow push.** **Settings → Actions → General → Workflow permissions** → choose **Read and write permissions** → Save.
5. **Turn on the website.** **Settings → Pages** → Source: **Deploy from a branch** → Branch: `main`, folder `/ (root)` → Save.
   Your site will be at `https://<your-username>.github.io/<repo-name>/`.
6. **Run it once.** **Actions → Update fantasy research → Run workflow** (mode: `full`).
   When it finishes (a few minutes), refresh the site.

## Settings

Edit `config.json`:

- `model`: the Claude model used for research. Check docs.claude.com for current model names.
- `max_web_searches`: cap on searches per research run (controls cost).
- `min_hours_between_research`: cooldown for change-triggered runs.
- `team_id`: your team's ESPN ID (Mr. Chow is 12).

## Notes and limits

- **Cost:** each research run is one Claude request plus up to `max_web_searches` searches. The default schedule is 5 full runs a week plus change-triggered ones. Check current prices on Anthropic's pricing page and set a spending limit in the Anthropic console.
- **Free plan = public repo.** GitHub Free only offers Pages for public repositories, so make the repo public. Your API key stays hidden as a secret; everything else (code, league data, reports) is visible, which is fine for fantasy data. A private repo with Pages needs a paid GitHub plan, and the site itself is public either way.
- **ESPN's feed is unofficial.** If ESPN changes it, `fetch_league.py` may need a fix. The free-agent request in particular relies on ESPN's `X-Fantasy-Filter` header.
- **The league must stay public.** If it goes private, the fetch will fail.
- **Commits:** the 4-hour check commits a fresh `league.json` each time, so the repo history will grow. That's harmless.
