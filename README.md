# frontier-flights

Unofficial, **pure-HTTP** Python SDK + CLI for scraping Frontier Airlines
flight availability — cash fares **and** miles cost — with no browser and no
login required.

It talks to the same backend endpoints that `www2.flyfrontier.com` uses
(Navitaire New Skies behind an Azure API Management gateway), reverse-engineered
from the site's network traffic.

> ⚠️ **Disclaimer.** This is an unofficial tool, not affiliated with or endorsed
> by Frontier Airlines. It queries publicly reachable, unauthenticated
> endpoints, but you are responsible for using it in line with Frontier's Terms
> of Use and applicable law. Scrape gently (keep the built-in request delay),
> don't hammer the API, and don't resell the data. Endpoints may change or break
> at any time.

---

## Repository layout

```
frontier-sdk/
├── frontier_flights/
│   ├── __init__.py     # public exports (FrontierClient, Airport, DayFare, Flight)
│   ├── client.py       # core pure-HTTP client: config bootstrap, anon-token
│   │                   #   mint/refresh, origins/destinations, lowfare_calendar,
│   │                   #   flights (nonstop detail). All endpoint constants live here.
│   ├── crawl.py        # Crawler: resumable, concurrent, network-wide scrape -> SQLite
│   └── cli.py          # argparse CLI: `routes`, `lowfares`, `flights`, `crawl`
├── examples/
│   ├── cheap_nonstop_from_den.py   # library usage demo
│   └── explore_dataset.py          # stats + best_deals.csv export from a crawl DB
├── README.md           # this file (full endpoint reference in "How it works")
├── pyproject.toml      # packaging; exposes `frontier-flights` console script
└── requirements.txt    # single dep: requests
```

**Where to start reading:** `client.py` defines every endpoint and the auth
model (top-of-file constants + docstrings). `crawl.py` is the bulk scraper.
`cli.py` wires both to the command line. There is **no hidden state** — the
subscription key is fetched live and the anon token is minted on demand, so the
client works from a clean checkout with no secrets or config.

## Current data snapshot

A completed crawl already exists in this directory (git-ignored, not committed):

* **`us_lowfares.db`** — SQLite, ~144k rows across all **4,116 US->US routes**,
  travel dates **2026-07-02 .. 2026-08-05**. See schema under
  *Network-wide crawling* below.
* **`best_deals.csv`** — cheapest cash fare per route (from `explore_dataset.py`).

The crawl is **resumable**: re-running `crawl` with a later `--end`/`--days`
into the same `us_lowfares.db` fills only the missing date windows. Regenerate
stats/exports any time with `python examples/explore_dataset.py us_lowfares.db`.

---

## Install

```powershell
cd frontier-sdk
python -m pip install -r requirements.txt
# optional: install as a package (provides the `frontier-flights` command)
python -m pip install -e .
```

Requires Python ≥ 3.9 and the `requests` library.

---

## Quick start (library)

```python
from frontier_flights import FrontierClient

client = FrontierClient()          # auto-fetches the API key, mints a token

# 1. Cheapest cash + miles per day (the "low-fare calendar")
for day in client.lowfare_calendar("DEN", "LAS", "2026-08-02", "2026-08-15"):
    print(day.date, day.cheapest_cash, "cash |", day.total_miles, "miles")

# 2. Individual NONSTOP flights on a date (with fares + miles)
for f in client.flights("DEN", "LAS", "2026-08-05", nonstop_only=True):
    print(f"FR{f.flight_number} {f.depart_time[11:16]} "
          f"${f.cheapest_cash} / {f.miles} miles / stops={f.stops}")

# 3. The route map
for dest in client.destinations("DEN"):
    print("DEN ->", dest.code, dest.city)
```

---

## Quick start (CLI)

```powershell
# Dump the full origin->destination route map
python -m frontier_flights.cli routes --out routes.csv

# Cheapest days for specific routes, next 30 days, filter to good deals
python -m frontier_flights.cli lowfares --routes DEN-LAS,DEN-PHX `
    --begin 2026-08-01 --days 30 --max-price 60 --max-miles 5000 `
    --out lowfares.csv --db frontier.db

# Expand one origin to ALL its destinations
python -m frontier_flights.cli lowfares --origins DEN --days 30 --out den_all.csv

# Individual nonstop flights for chosen routes/dates
python -m frontier_flights.cli flights --routes DEN-LAS `
    --date 2026-08-05,2026-08-12 --nonstop --out flights.csv
```

Every command writes **CSV** (`--out`) and/or **SQLite** (`--db`). With neither,
the first 50 rows print to stdout as CSV. Rows are sorted cheapest-first.

If installed as a package, replace `python -m frontier_flights.cli` with
`frontier-flights`.

---

## Network-wide crawling (build a dataset)

The `crawl` command scrapes low-fares across the **entire US route network**
into a single resumable SQLite database. It:

* discovers every US->US directed route (cached in the DB),
* fetches the per-day cheapest cash + miles fares over your date range,
* commits every 7-day window immediately (crash-safe),
* **resumes** automatically — re-run the exact command to continue,
* runs concurrently (default 8 workers) and skips invalid markets.

```powershell
# Full US network, next 60 days, into frontier.db  (~1.5-2 hours)
python -m frontier_flights.cli crawl --db frontier.db --days 60 --workers 8

# Whole bookable window (today .. a fixed far date)
python -m frontier_flights.cli crawl --db frontier.db --begin 2026-07-03 --end 2026-11-19

# Limit to specific origins
python -m frontier_flights.cli crawl --db frontier.db --origins DEN,MCO,LAS --days 30
```

Interrupt any time with Ctrl+C — progress is saved. Re-running skips completed
windows, so you can crawl in sessions.

**Rough runtime** (8 workers, ~4,100 US routes): ~1 min per day-of-range for the
first ~7 days, then ~1 min per additional 7-day window across the network — about
**1 hour per 30 days** of travel dates.

### The dataset (SQLite schema)

| table | contents |
|---|---|
| `lowfares` | `(origin, dest, date, standard_fare, discounted_fare, gowild_fare, total_miles, miles_taxes_fees, currency, scraped_at)` — the data |
| `airports` | airport reference data (code, city, country, lat/long) |
| `routes` | route map + market-validity cache (`valid=0` = no such market) |
| `crawl_windows` | resume ledger (which 7-day windows are done) |
| `crawl_meta` | run metadata |

Explore it with any SQLite tool, pandas, or DuckDB, e.g.:

```sql
-- cheapest nonstop-eligible cash deals in the network
SELECT origin, dest, date, discounted_fare, total_miles
FROM lowfares
WHERE discounted_fare IS NOT NULL
ORDER BY discounted_fare
LIMIT 25;

-- best award (miles) value per route
SELECT origin, dest, MIN(total_miles) AS min_miles
FROM lowfares WHERE total_miles IS NOT NULL
GROUP BY origin, dest ORDER BY min_miles;
```

To confirm a shortlisted route/date is truly **nonstop** and get flight-level
detail, follow up with the `flights --nonstop` command or `client.flights(...)`.

---

## Output columns

**`lowfares`** (one row per route per day):

| column | meaning |
|---|---|
| `origin`, `destination` | airport codes |
| `date` | `YYYY-MM-DD` |
| `standard_fare` | standard economy cash fare |
| `discounted_fare` | discount-den / promo cash fare |
| `gowild_fare` | GoWild! pass fare (if offered that day) |
| `total_miles` | award cost in Frontier Miles |
| `miles_taxes_fees` | cash taxes/fees on the award |
| `currency` | fare currency |

**`flights`** (one row per scheduled flight):

| column | meaning |
|---|---|
| `flight_number` | e.g. `1993` (operated as `F9 1993`) |
| `depart_time`, `arrive_time` | ISO timestamps |
| `aircraft` | e.g. `A320`, `A321` |
| `stops` | `0` = nonstop |
| `flight_type` | `NonStop` / `Connecting` |
| `total_flight_time` | `HH:MM:SS` |
| `standard_fare`, `discounted_fare`, `gowild_fare` | cash fares |
| `miles` | award cost in Frontier Miles |

---

## How it works (reverse-engineering notes)

Three moving parts, all over plain HTTPS:

### 1. Config — the API key is public
`GET https://www2.flyfrontier.com/api/environment` returns a JSON blob that
includes `BFF_SUBSCRIPTION_KEY` (the Azure APIM product key) and `BFF_ENDPOINT`.
The key is shared by all anonymous web visitors — it is **not** tied to a user
account. The SDK fetches it automatically (override with
`--subscription-key` / `FrontierClient(subscription_key=...)`).

### 2. Auth — an anonymous token, minted on demand
The availability endpoints require three headers:

* `ocp-apim-subscription-key` — the key from step 1 (**required**).
* `authtoken` — a short-lived (~15 min) anonymous **Navitaire session JWT**
  (**required**).
* `frontiertoken` — **must be present but is not validated**; the SDK sends a
  random UUID.

The token is created with a single unauthenticated GraphQL call:

```graphql
query RetrieveAnonymousToken {
  uiRetrieveAnonymousTokenSetSingleton {
    data { authToken expires organizationCode roleCode ... }
  }
}
```

The returned token carries `userKey: null` / `org: SYSTEM` — i.e. it is a pure
anonymous session, unrelated to any logged-in account. The SDK mints it lazily
and auto-refreshes before expiry.

### 3. Data endpoints

| purpose | endpoint | type | auth |
|---|---|---|---|
| API config / key | `/api/environment` | REST GET | none |
| Mint anon token | `consumerappsbff/graphql` · `RetrieveAnonymousToken` | GraphQL | key only |
| Origin airports | `consumerappsbff/graphql` · `UiOriginAirportListSetSingleton` | GraphQL | key only |
| Destinations for an origin | `consumerappsbff/graphql` · `GetDestination` | GraphQL | key only |
| Per-day cheapest fares + miles | `flightavailabilityssv/NFAvailabilityLowfareSearch` | REST POST | key + token |
| Per-flight availability (stops, times, fares, miles) | `consumerappsbff/graphql` · `UpdateAvailability` | GraphQL | key + token |
| Per-day service/flight counts | `consumerappsbff/graphql` · `GetTripSchedule` | GraphQL | key + token |

Base host for both the BFF and REST services is `https://mtier.flyfrontier.com`.

**Two complementary fare sources.** The *low-fare calendar*
(`NFAvailabilityLowfareSearch`) is cheap and bulk — it returns the single
cheapest cash/miles figure per day for a route across a ~7-day window (the SDK
auto-chunks longer ranges). The *availability* call (`UpdateAvailability`)
returns every individual flight with its stop count, so it's what you use to
confirm a route is **nonstop** and to see per-flight times and prices.

---

## Recommended scraping strategy

To build a table of "cheap, direct, one-way flights" network-wide:

1. `routes` → get the route map (origins × destinations).
2. `lowfares` over your date window with `--max-price` / `--max-miles` to
   cheaply shortlist attractive route/date combinations.
3. `flights --nonstop` on the shortlisted route/date pairs to confirm nonstop
   service and capture exact flight-level fares/miles.

Keep the default `--delay` (0.3s) or raise it for large crawls. The token
refresh, retries, and 7-day chunking are handled for you.

---

## Programmatic API reference

`FrontierClient(subscription_key=None, bff_endpoint=None, currency="USD",
request_delay=0.3, timeout=30.0, max_retries=3)`

| method | returns |
|---|---|
| `origins()` | `list[Airport]` — all origin airports |
| `destinations(origin)` | `list[Airport]` — destinations from an origin |
| `routes(origins=None)` | iterator of `(origin, destination)` pairs |
| `trip_schedule(o, d, begin_date)` | raw per-day service rows |
| `lowfare_calendar(o, d, begin, end, fare_types=None)` | `list[DayFare]` |
| `flights(o, d, date, nonstop_only=False, adults=1)` | `list[Flight]` |

Dataclasses `Airport`, `DayFare`, and `Flight` expose convenience properties
like `DayFare.cheapest_cash`, `Flight.is_nonstop`, and `Flight.cheapest_cash`.

---

## Limitations

* One-way, single-adult pricing by default (multi-pax counts are supported by
  `flights(..., adults=n)`; the low-fare calendar is queried for 1 adult).
* Fares are indicative availability prices, exclusive of most bags/seats.
* The anonymous token and public key can be rotated or gated by Frontier at any
  time; the endpoints are undocumented and unsupported.
