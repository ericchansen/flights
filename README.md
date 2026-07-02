# flights

Unofficial, **pure-HTTP**, **multi-provider** Python SDK + CLI for scraping
airline flight availability — cash fares **and** award (miles) cost — with no
browser and no login.

Each airline is a **provider** implementing a common interface, so the same
models, CLI, and network crawler work across carriers. The first provider is
**Frontier Airlines** (reverse-engineered from `www2.flyfrontier.com`).

> ⚠️ **Disclaimer.** Unofficial tool, not affiliated with or endorsed by any
> airline. It queries publicly reachable, unauthenticated endpoints, but you are
> responsible for using it within each carrier's Terms of Use and applicable
> law. Scrape gently, don't hammer the APIs, and don't resell the data.
> Undocumented endpoints may change or break at any time.

---

## Repository layout

```
flights/
├── src/flights/
│   ├── __init__.py            # top-level API: get_provider(), models, Crawler
│   ├── cli.py                 # provider-aware CLI (routes/lowfares/flights/crawl)
│   ├── core/                  # provider-AGNOSTIC building blocks
│   │   ├── models.py          #   Airport, DayFare, Flight (normalized shapes)
│   │   ├── provider.py        #   BaseProvider ABC — the interface to implement
│   │   ├── crawl.py           #   generic resumable/concurrent SQLite crawler
│   │   ├── registry.py        #   get_provider() / register_provider()
│   │   └── errors.py          #   FlightsError, ProviderError, MarketNotFoundError
│   └── providers/             # one subpackage per airline
│       ├── __init__.py        #   registers bundled providers
│       └── frontier/          #   FrontierProvider(BaseProvider) + endpoint logic
├── examples/
│   ├── cheap_nonstop_from_den.py
│   └── explore_dataset.py     # stats + best_deals.csv from a crawl DB
├── pyproject.toml             # src layout; console script `flights`
├── requirements.txt           # single dep: requests
├── LICENSE                    # MIT
└── README.md
```

**Where to start reading:** `core/provider.py` (the interface) →
`providers/frontier/client.py` (a concrete implementation with the full
endpoint/auth details) → `core/crawl.py` (the generic bulk scraper). There is
**no hidden state**: keys are fetched live and tokens minted on demand, so the
package works from a clean checkout with no secrets or config.

---

## Install

```powershell
python -m pip install -r requirements.txt      # just needs `requests`
python -m pip install -e .                      # optional: `flights` console command
```

Requires Python ≥ 3.9.

---

## Quick start (library)

```python
from flights import get_provider

airline = get_provider("frontier")      # auto-fetches the API key, mints a token

# 1. Cheapest cash + miles per day (the low-fare calendar)
for day in airline.lowfare_calendar("DEN", "LAS", "2026-08-02", "2026-08-15"):
    print(day.date, day.cheapest_cash, "cash |", day.miles, "miles")

# 2. Individual NONSTOP flights on a date (fares + miles)
for f in airline.flights("DEN", "LAS", "2026-08-05", nonstop_only=True):
    print(f"FR{f.flight_number} {f.depart_time[11:16]} "
          f"${f.cheapest_cash} / {f.miles} miles / stops={f.stops}")

# 3. The route map
for dest in airline.destinations("DEN"):
    print("DEN ->", dest.code, dest.city)
```

Every value is a normalized model (`Airport`, `DayFare`, `Flight`) carrying a
`provider` field, so results from different airlines mix cleanly.

---

## Quick start (CLI)

```powershell
# (all commands accept --provider, default: frontier)

# Full origin->destination route map
python -m flights.cli routes --out routes.csv

# Cheapest days for specific routes, next 30 days, filter to good deals
python -m flights.cli lowfares --routes DEN-LAS,DEN-PHX `
    --begin 2026-08-01 --days 30 --max-price 60 --max-miles 5000 --out fares.csv

# Expand one origin to ALL its destinations
python -m flights.cli lowfares --origins DEN --days 30 --out den_all.csv

# Individual nonstop flights for chosen routes/dates
python -m flights.cli flights --routes DEN-LAS `
    --date 2026-08-05,2026-08-12 --nonstop --out flights.csv
```

Output goes to CSV (`--out`) and/or SQLite (`--db`); with neither, the first 50
rows print as CSV. If installed, use the `flights` command instead of
`python -m flights.cli`.

---

## Network-wide crawling (build a dataset)

The `crawl` command scrapes low-fares across an entire US route network into a
resumable SQLite database. It discovers every US->US route, fetches the per-day
cheapest cash + miles fares, commits each 7-day window immediately (crash-safe),
**resumes** on re-run, runs concurrently (default 8 workers), and skips invalid
markets.

```powershell
# Full US network, next 30 days  (~1 hour at 8 workers, ~4,100 routes)
python -m flights.cli crawl --db flights.db --days 30 --workers 8

# A fixed window; interrupt with Ctrl+C and re-run to resume
python -m flights.cli crawl --db flights.db --begin 2026-07-03 --end 2026-08-31

# Limit origins
python -m flights.cli crawl --db flights.db --origins DEN,MCO,LAS --days 30
```

### Dataset schema (SQLite)

| table | contents |
|---|---|
| `lowfares` | `(provider, origin, destination, date, standard_fare, discounted_fare, saver_fare, miles, miles_fees, currency, scraped_at)` |
| `airports` | airport reference data |
| `routes` | route map + market-validity cache (`valid=0` = no such market), per provider |
| `crawl_windows` | resume ledger (which windows are done, with the covered `window_end`) |
| `crawl_meta` | run metadata |

The `provider` column means one DB can hold multiple airlines side by side.
Column names match the `DayFare` / `Flight` model fields (e.g. `destination`),
so the ad-hoc `lowfares` CLI export and the crawler dataset share one schema.
The `routes` CLI export uses a separate `route_map` table so it never collides
with the crawler's `routes` validity cache.

```sql
-- cheapest cash deals network-wide
SELECT provider, origin, destination, date,
       MIN(COALESCE(standard_fare,1e9), COALESCE(discounted_fare,1e9), COALESCE(saver_fare,1e9)) AS fare,
       miles
FROM lowfares GROUP BY provider, origin, destination, date
ORDER BY fare LIMIT 25;
```

Explore/export with `python examples/explore_dataset.py flights.db`.

---

## Fare/flight columns

`DayFare`: `provider, origin, destination, date, standard_fare,
discounted_fare, saver_fare, miles, miles_fees, currency` (+ `.cheapest_cash`).

`Flight`: `provider, origin, destination, date, flight_number, depart_time,
arrive_time, aircraft, stops, flight_type, duration, standard_fare,
discounted_fare, saver_fare, miles, currency` (+ `.is_nonstop`, `.cheapest_cash`).

`saver_fare` is the lowest promotional/basic tier where a carrier offers one
(for Frontier this is the GoWild! fare). `stops == 0` (or `flight_type`
`NonStop`) marks a direct flight.

---

## Adding a new provider

1. Create `src/flights/providers/<airline>/client.py` with a class that
   subclasses `BaseProvider` and implements the four required methods:

   ```python
   from flights.core import BaseProvider, Airport, DayFare, Flight, MarketNotFoundError

   class ExampleProvider(BaseProvider):
       name = "example"
       lowfare_window_days = 7

       def origins(self): ...
       def destinations(self, origin): ...
       def lowfare_window(self, origin, destination, begin, end): ...   # raise MarketNotFoundError if invalid
       def flights(self, origin, destination, date, nonstop_only=False, adults=1): ...
   ```

   `lowfare_calendar`, `routes`, and `us_origins` are provided by the base class.
   Make the provider thread-safe (lock token refresh, thread-local session) so
   it can back the concurrent crawler.

2. Register it in `src/flights/providers/__init__.py`:

   ```python
   from .example import ExampleProvider
   register_provider("example", ExampleProvider)
   ```

That's it — `get_provider("example")`, `--provider example`, and
`crawl --provider example` all work immediately.

---

## How Frontier works (reverse-engineering notes)

All over plain HTTPS; the details live in `providers/frontier/client.py`.

**1. Config — the API key is public.** `GET
https://www2.flyfrontier.com/api/environment` returns `BFF_SUBSCRIPTION_KEY`
(shared Azure APIM key, not per-user) and `BFF_ENDPOINT`. Fetched automatically.

**2. Auth — an anonymous token, minted on demand.** The availability endpoints
need three headers: `ocp-apim-subscription-key` (the key), `authtoken` (a
short-lived ~15-min anonymous Navitaire session JWT), and `frontiertoken`
(required but **not validated** — a random UUID). The token is created with a
single unauthenticated GraphQL call `RetrieveAnonymousToken`
(`userKey: null` / `org: SYSTEM` — pure anonymous session) and auto-refreshed.

**3. Data endpoints** (base host `https://mtier.flyfrontier.com`):

| purpose | endpoint / op | auth |
|---|---|---|
| config / key | `/api/environment` | none |
| mint token | `consumerappsbff/graphql` · `RetrieveAnonymousToken` | key only |
| origins | `consumerappsbff/graphql` · `UiOriginAirportListSetSingleton` | key only |
| destinations | `consumerappsbff/graphql` · `GetDestination` | key only |
| per-day cheapest fares + miles | `flightavailabilityssv/NFAvailabilityLowfareSearch` | key + token |
| per-flight availability (stops, times, fares, miles) | `consumerappsbff/graphql` · `UpdateAvailability` | key + token |

The low-fare calendar returns the single cheapest cash/miles figure per day over
a ~7-day window (longer ranges are auto-chunked); the availability call returns
every flight with its stop count, used to confirm **nonstop** service.

---

## Limitations

* One-way, single-adult pricing by default (`flights(..., adults=n)` supported).
* Fares are indicative availability prices, exclusive of most bags/seats.
* Anonymous tokens / public keys can be rotated or gated by a carrier at any
  time; endpoints are undocumented and unsupported.
