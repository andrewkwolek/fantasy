# Fantasy League HQ

A local website for an ESPN fantasy football league. It scrapes every season your
league has ever played into a local SQLite database, then serves a site with the
advanced metrics ESPN doesn't show you — all-play records, schedule luck, manager
efficiency, power rankings, Monte Carlo playoff odds, trade forensics, draft
grades, and a full record book.

## Quick start

```bash
pip install --user -r requirements.txt      # or use a virtualenv
cp .env.example .env                        # then fill in your league ID
python3 -m fantasy.cli scrape               # pulls every season ESPN still has
python3 -m fantasy.cli serve                # http://127.0.0.1:8000
```

### Configuring `.env`

```ini
LEAGUE_ID=123456       # from https://fantasy.espn.com/football/league?leagueId=123456
ESPN_S2=               # private leagues only
SWID=                  # private leagues only
FIRST_SEASON=          # optional floor; blank = probe back until ESPN 404s
```

**Private leagues** need two browser cookies. On `fantasy.espn.com` while logged
in, open DevTools → Application → Cookies → `https://fantasy.espn.com`, and copy
the `espn_s2` and `SWID` values. `SWID` normally includes the surrounding braces;
either form works. `.env` is gitignored.

## Commands

| Command | What it does |
|---|---|
| `python3 -m fantasy.cli scrape` | Auto-discovers every season and pulls it |
| `... scrape --seasons 2024 2025` | Only those years |
| `... scrape --refresh` | Ignore the local raw cache and re-fetch from ESPN |
| `python3 -m fantasy.cli serve` | Run the site (`--port`, `--reload`) |
| `python3 -m fantasy.cli report` | Print the latest weekly recap to the terminal |
| `python3 -m fantasy.cli demo` | Generate a synthetic league (no ESPN needed) |

Re-run `scrape` weekly during the season. Raw ESPN responses are cached under
`data/raw/`, and the season still in progress is always re-fetched while completed
seasons are served from that cache — so a weekly update is quick without ever
showing stale standings. `--refresh` forces everything.

## League rules this encodes

Two rules specific to this league are applied on top of ESPN's data:

**Final placings.** Only the playoff bracket moves a team. Teams that miss the
playoffs are placed by regular-season record alone — consolation ("toilet bowl")
results never change a final standing. ESPN disagrees: its `rankCalculatedFinal`
reorders the bottom half by the consolation ladder, which in 2025 had the 7th-best
regular-season team listed 12th and the 12th listed 7th. The site overrides that
and shows the league's own placing.

Consolation games are treated as exhibitions throughout: they are excluded from
all-play, expected wins, luck, career records, playoff records, the record book
and the all-time head-to-head grid. They still appear on team and week pages,
marked `C`, because they were played.

**Median scoring** (`MEDIAN_SCORING_FROM` in `.env`, set to the first season it
applies). Two wins are available each week: one for winning your matchup, one for
outscoring the league median. Standings are ordered by the combined total, which
is also what playoff seeding and the non-playoff placings key off. Regular season
only — the playoffs stay head-to-head. This is the same idea the all-play and luck
columns measure, now baked into the standings: a team cannot be buried by its
schedule alone.

## What the metrics mean

| Metric | Definition |
|---|---|
| **All-play** | Your record if you had played *every* team every week. Removes schedule luck entirely. |
| **Expected wins (xW)** | All-play win rate × games played — the wins your scoring earned. |
| **Luck** | Actual wins − expected wins. Positive means the schedule was kind. Sums to ~0 league-wide. |
| **Median record** | Weeks you outscored the league median (median scoring seasons only). |
| **Combined** | Matchup record plus median record — the standings order. |
| **Optimal points** | The highest score your roster could have produced that week, solved exactly (see below). |
| **Efficiency** | Actual points ÷ optimal points. Pure lineup-setting skill. |
| **Bench points** | Optimal − actual: what you left sitting. |
| **SoS** | Average score your opponents put up against you. |
| **Power score** | 0–100: season scoring 30%, recent form 25%, all-play 30%, consistency 10%, efficiency 5%. Each component is converted to a within-league percentile before weighting. Record is deliberately excluded — it is the noisiest signal in fantasy. |
| **Playoff odds** | 10,000 Monte Carlo seasons. Each team's weekly score is drawn from its own mean/σ, shrunk toward the league average so a 2-game sample isn't treated as known. |
| **Trade production** | Points the acquired players scored from the trade week onward. **Contribution** counts only points scored while actually in the new team's starting lineup. |
| **Draft value** | Draft slot − where that player actually finished among all drafted players. Pick 90 finishing 20th is +70. |

### The optimal-lineup solver

"Points left on the bench" is only meaningful against a *true* optimum. A greedy
fill (best QB, best RB, … then flex) is not one: it can strand a high-scoring
flex-eligible player behind a slot decision made earlier. `fantasy/analytics/
lineup.py` solves it exactly as a max-weight bipartite matching between lineup
slots and rostered players (Hungarian algorithm), respecting each player's real
`eligibleSlots`.

## Layout

```
fantasy/
  config.py         .env loading
  cli.py            scrape / serve / report / demo
  scrape.py         orchestration: season -> tables
  demo.py           synthetic league in ESPN's exact shape
  espn/
    client.py       HTTP, auth, retry, raw-response cache, season discovery
    constants.py    ESPN's integer IDs -> names
    parse.py        raw JSON -> flat rows
  store/db.py       SQLite schema, idempotent upserts, franchise stitching
  analytics/
    core.py         the team-week fact table everything aggregates over
    lineup.py       exact optimal-lineup solver
    standings.py    season standings, advanced columns
    power.py        power rankings
    history.py      all-time standings, head-to-head, record book
    trades.py       trade forensics
    draft.py        draft grades, steals/busts, waiver hit rate
    projections.py  Monte Carlo playoff odds
    reports.py      weekly recap
  web/
    app.py          FastAPI routes + JSON API
    charts.py       server-rendered inline SVG charts
    templates/      Jinja2
    static/         CSS + a small tooltip/highlight script
```

Franchises are keyed by ESPN's owner GUID, not team ID, so a manager keeps one
career line across seasons even after renaming the team every August.

## Publishing to GitHub Pages

The `main` branch holds source code, **not** the built site. Pointing Pages at
`main / (root)` therefore publishes nothing useful — GitHub's Jekyll finds no
`index.html` and renders `README.md` as the homepage instead. There are two
correct ways to publish.

### Recommended: deploy from Actions

Set **Settings → Pages → Source: GitHub Actions**. The workflow in
`.github/workflows/weekly-update.yml` scrapes, builds and uploads `dist/` as a
Pages artifact, so no branch ever has to contain generated files. Nothing else
to configure, and it keeps itself up to date (see below).

### Alternative: publish the build to its own branch

If you would rather not use Actions, push only the *contents of `dist/`* to a
`gh-pages` branch and point Pages at that branch:

```bash
python3 -m fantasy.cli export --base-path /<repo>
cd dist
git init -b gh-pages && git add -A && git commit -m "Publish league site"
git remote add origin git@github.com:<you>/<repo>.git
git push -f origin gh-pages
```

Then **Settings → Pages → Source: Deploy from a branch → `gh-pages` / `(root)`**.
Note this pushes `dist/`, not the repository root — that distinction is the whole
point.

### Automatic weekly updates

`.github/workflows/weekly-update.yml` scrapes ESPN and republishes the site every
Tuesday morning, after Monday Night Football. To enable it:

1. **Settings → Secrets and variables → Actions → Secrets**, add `LEAGUE_ID`,
   `ESPN_S2` and `SWID`.
2. Optionally add a **Variable** `MEDIAN_SCORING_FROM` (e.g. `2026`).
3. **Settings → Pages → Source: GitHub Actions.**
4. Set the cron hour for your timezone — GitHub cron is UTC and ignores daylight
   saving, so the workflow header lists the UTC hour for 4am in each US zone.

Run it by hand any time from **Actions → Weekly league update → Run workflow**.

The job re-reads the season in progress every run but restores completed seasons
from a cache, so a weekly update costs ESPN about 25 requests rather than 90. It
fails loudly on expired cookies (refresh the two secrets), on a suspiciously small
build, and if either credential is ever found in the output.

Two GitHub behaviours worth knowing: scheduled workflows are **disabled after 60
days without repository activity** (re-enable from the Actions tab), and cron runs
can be delayed when GitHub is busy — the time is a floor, not a guarantee.

### Privacy

**A GitHub Pages site is public to anyone with the URL** (private-repo Pages with
access control is an Enterprise feature). The site shows managers' real names, team
names and results. Two things follow:

- The export writes `<meta name="robots" content="noindex, nofollow">` so search
  engines are asked not to list it. Pass `--allow-indexing` to drop that.
- **No credential ever reaches the export.** Franchise URLs and the exported JSON
  use name slugs (`/franchise/andrew-kwolek`), never the ESPN member GUID — that
  GUID is the account's `SWID` cookie, half of the API credential pair. The build
  is checked for this. Team logos still point at ESPN's public CDN.

## JSON API

`/api/standings/{season}`, `/api/power/{season}`, `/api/odds/{season}`,
`/api/records`, `/api/trades`.

## Notes and limits

- **Forfeits.** If ESPN reports a team's entire starting lineup as `0.00` while
  its bench scored normally, that is a forfeit or commissioner zero-out, not a
  lineup decision. Those weeks still count in the standings, scoring and record
  book (the team really did score nothing and lose), but they are excluded from
  manager efficiency, which measures lineup choices. The team page labels the
  week `forfeit`.
- ESPN's detailed views (player-level box scores, draft, transactions) are
  reliable from **2018** onward. Older seasons usually return standings and
  scores but little else, so efficiency, trades and draft pages may be empty for
  them. The scraper degrades per-season rather than failing.
- Charts are server-rendered SVG with no external dependencies, so the site works
  offline once scraped and themes itself for light and dark.
- The colour palette is validated against the six colour-vision checks
  (lightness band, chroma floor, CVD separation, normal-vision floor, contrast).
  Charts with 10+ teams deliberately use one neutral ink plus hover highlighting
  rather than 10 hues, because no categorical palette separates 10 series.
