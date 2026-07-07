"""CLI entry points for the fantasy trade bot."""
import argparse
import sys

from src.db import init_db
from src.crosswalk import build_crosswalk
from src.fantasycalc_api import fetch_and_cache
from src.value_engine import dump_csv, get_value_map, make_pick_resolver, get_pick_value_map
from src.trade_scorer import score_trade, format_result


def main():
    parser = argparse.ArgumentParser(description="Fantasy Trade Bot")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("init", help="Initialize DB and build crosswalk")
    sub.add_parser("values", help="Fetch values and dump player_values.csv")

    score_p = sub.add_parser("score", help="Score a trade")
    score_p.add_argument("--side1", required=True, help="Comma-separated MFL player IDs")
    score_p.add_argument("--side2", required=True, help="Comma-separated MFL player IDs")
    score_p.add_argument("--owner1", help="Side 1 franchise ID (enables positional-fit check)")
    score_p.add_argument("--owner2", help="Side 2 franchise ID (enables positional-fit check)")

    sub.add_parser("scan", help="Scan league trades (Phase 3)")
    sub.add_parser("waivers", help="Scan waiver gems")
    sub.add_parser("report", help="Push Discord weekly report")
    tile_p = sub.add_parser("tile", help="Write/serve Homarr status tile")
    tile_p.add_argument("--serve", action="store_true", help="Run Flask server")

    args = parser.parse_args()

    if args.command == "init":
        init_db()
        print("DB initialized.")
        fetch_and_cache()
        print("FantasyCalc values cached.")
        build_crosswalk()

    elif args.command == "values":
        init_db()
        dump_csv()

    elif args.command == "scan":
        from src.scanner import scan_trades
        results = scan_trades()
        if not results:
            print("No new trades since last scan.")
        else:
            lopsided = [r for r in results if r["lopsided"]]
            print(f"Scanned {len(results)} new trade(s); {len(lopsided)} lopsided.\n")
            for r in results:
                res = r["result"]
                tag = "  <<< LOPSIDED" if r["lopsided"] else ""
                print(f"[{r['franchise1']} <-> {r['franchise2']}] "
                      f"{res.verdict} (gap {res.value_delta_pct*100:.0f}%){tag}")

    elif args.command == "score":
        init_db()
        value_map = get_value_map()
        pick_resolver = make_pick_resolver(get_pick_value_map())
        thin_lookup = None
        if args.owner1 or args.owner2:
            from src.roster import thin_positions
            thin_lookup = lambda fid: thin_positions(fid, value_map)
        result = score_trade(
            [s.strip() for s in args.side1.split(",") if s.strip()],
            [s.strip() for s in args.side2.split(",") if s.strip()],
            value_map,
            side1_owner=args.owner1,
            side2_owner=args.owner2,
            thin_lookup=thin_lookup,
            pick_resolver=pick_resolver,
        )
        print(format_result(result))

    elif args.command == "waivers":
        init_db()
        from src.waivers import waiver_gems, format_waiver_report
        report = waiver_gems()
        print(format_waiver_report(report))

    elif args.command == "report":
        init_db()
        from src.discord_report import run_weekly
        run_weekly()

    elif args.command == "tile":
        init_db()
        from src.homarr_tile import write_status, app as flask_app
        if args.serve:
            import os
            flask_app.run(host="0.0.0.0", port=int(os.getenv("HOMARR_PORT", "5055")))
        else:
            status = write_status()
            import json
            print(json.dumps(status, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
