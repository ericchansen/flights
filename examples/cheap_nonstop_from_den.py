"""Example: find the cheapest nonstop days from Denver over the next 45 days.

Run:  python examples/cheap_nonstop_from_den.py
"""

from frontier_flights import FrontierClient


def main() -> None:
    client = FrontierClient()

    # A few popular Frontier nonstop markets from its Denver hub.
    targets = ["LAS", "PHX", "MCO", "SAN", "ATL"]

    print("Cheapest cash fare per destination (next 30 days), DEN ->")
    for dest in targets:
        fares = client.lowfare_calendar("DEN", dest, _today(), _plus(30))
        priced = [f for f in fares if f.cheapest_cash is not None]
        if not priced:
            print(f"  DEN-{dest}: no fares")
            continue
        best = min(priced, key=lambda f: f.cheapest_cash)
        print(
            f"  DEN-{dest}: ${best.cheapest_cash:6.2f} on {best.date} "
            f"| miles {best.total_miles} + ${best.miles_taxes_fees}"
        )

    # Drill into one date for actual nonstop flights.
    print("\nNonstop DEN-LAS flights on the cheapest LAS date:")
    fares = client.lowfare_calendar("DEN", "LAS", _today(), _plus(30))
    best_day = min(
        (f for f in fares if f.cheapest_cash is not None),
        key=lambda f: f.cheapest_cash,
    )
    for fl in client.flights("DEN", "LAS", best_day.date, nonstop_only=True):
        print(
            f"  FR{fl.flight_number} {fl.depart_time[11:16]}->{fl.arrive_time[11:16]} "
            f"{fl.aircraft} | ${fl.cheapest_cash} | {fl.miles} miles"
        )


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


def _plus(days: int) -> str:
    import datetime
    return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()


if __name__ == "__main__":
    main()
