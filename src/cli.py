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

    exp_p = sub.add_parser("explain", help="Show how a player's projection is scored")
    exp_p.add_argument("player", help="Player name (fuzzy match ok)")
    exp_p.add_argument("--week", type=int, help="Score a week instead of the season")

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
            from src.mfl_api import franchise_name
            lopsided = [r for r in results if r["lopsided"]]
            print(f"Scanned {len(results)} new trade(s); {len(lopsided)} lopsided.\n")
            for r in results:
                res = r["result"]
                tag = "  <<< LOPSIDED" if r["lopsided"] else ""
                print(f"[{franchise_name(r['franchise1'])} <-> {franchise_name(r['franchise2'])}] "
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

    elif args.command == "explain":
        init_db()
        from src.crosswalk import resolve_player
        from src.scoring import fetch_rules, explain_points, rules_for_position
        from src.sleeper_xwalk import get_sleeper_map
        from src.sleeper_api import get_nfl_state, get_projections
        from src.projections import _sleeper_season
        from src import mfl_api

        cands = resolve_player(args.player, limit=1)
        if not cands:
            print(f"No player matching {args.player!r}")
            return
        c = cands[0]
        position = next((p.get("position") for p in mfl_api.get_players()
                         if p.get("id") == c["mfl_id"]), None)
        sid = get_sleeper_map().get(c["mfl_id"])
        if not sid:
            print(f"{c['mfl_name']}: no Sleeper id in the crosswalk.")
            return

        state = get_nfl_state()
        season = int(state["season"])
        if args.week:
            row = get_projections(season, args.week).get(sid)
            scope = f"week {args.week}"
        else:
            row = _sleeper_season(season).get(sid)
            scope = "season"
        if not row:
            print(f"{c['mfl_name']}: no Sleeper projection for {scope}.")
            return

        rules = fetch_rules()
        scoped = rules_for_position(rules, position)
        rows = explain_points(row, rules, position, season=not args.week)
        print(f"{c['mfl_name']} ({position}) — {season} {scope}")
        print(f"  {len(scoped)} of {len(rules)} scoring rules apply to {position}")
        print(f"  {'EVENT':<6} {'STAT':<16} {'PROJECTED':>10} {'POINTS':>9}")
        for r in rows:
            print(f"  {r['event']:<6} {r['stat']:<16} {r['amount']:>10.1f} {r['points']:>9.2f}")
        print(f"  {'':<6} {'':<16} {'TOTAL':>10} {sum(r['points'] for r in rows):>9.2f}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
