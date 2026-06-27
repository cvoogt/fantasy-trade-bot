"""CLI entry points for the fantasy trade bot."""
import argparse
import sys

from src.db import init_db
from src.crosswalk import build_crosswalk
from src.fantasycalc_api import fetch_and_cache
from src.value_engine import dump_csv, get_value_map
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
    sub.add_parser("waivers", help="Scan waiver gems (Phase 4)")
    sub.add_parser("report", help="Push Discord report (Phase 5)")

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

    elif args.command == "score":
        init_db()
        value_map = get_value_map()
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
        )
        print(format_result(result))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
