"""
Top-level entry point for the Bookeo -> SCS (Reserve) migration pipeline.

Thin CLI wrapper around migration/push.py -- see Project_details.txt for the
full picture (temporary-hold-only push, dedupe/merge handling, commit/cleanup
steps). Nothing here bypasses that safety model: `push` only ever creates
Temporary Holds, `commit` is the sole way holds become permanent, `cleanup`
sweeps holds away.

bookeo/bookeo_api.py and Reserve/scs_gateway.py each call load_dotenv() with
no path, which only searches upward from the current working directory --
it will NOT find bookeo/.env or Reserve/.env when run.py is invoked from the
project root. So this loads both .env files explicitly, by path, before
touching either client.

Setup (once per machine):
    pip install -r bookeo/requirements.txt
    pip install -r Reserve/requirements.txt

Usage:
    python run.py check                                   # credential/connectivity sanity check, both sides
    python run.py push <start_iso> <end_iso> [--limit N] [--cleanup-after]
    python run.py cleanup
    python run.py commit
"""

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
BOOKEO_DIR = os.path.join(ROOT, "bookeo")
RESERVE_DIR = os.path.join(ROOT, "Reserve")
MIGRATION_DIR = os.path.join(ROOT, "migration")

sys.path.insert(0, MIGRATION_DIR)
sys.path.insert(0, BOOKEO_DIR)
sys.path.insert(0, RESERVE_DIR)


def _load_env_files():
    from dotenv import load_dotenv

    for env_dir in (BOOKEO_DIR, RESERVE_DIR):
        env_path = os.path.join(env_dir, ".env")
        if not os.path.exists(env_path):
            print(f"warning: {env_path} not found -- see each project's readme.md Setup section", file=sys.stderr)
            continue
        load_dotenv(env_path)


def run_check():
    """Sanity-check credentials/connectivity against Bookeo and SCS. Read-only."""
    from bookeo_api import BookeoAPI
    from scs_gateway import SCSGatewayClient

    bookeo = BookeoAPI()
    info = bookeo.test_connection()
    print("Bookeo connection OK:", info)

    scs = SCSGatewayClient()
    health = scs.health_check()
    print("SCS Gateway connection OK:", health)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="Read-only credential/connectivity check against Bookeo and SCS")

    push_cmd = sub.add_parser("push", help="Pull Bookeo bookings in a window and push them as SCS temporary holds")
    push_cmd.add_argument("start_time", help="Bookeo ISO8601 window start, e.g. 2026-08-21T00:00:00Z")
    push_cmd.add_argument("end_time", help="Bookeo ISO8601 window end (max 31 days after start)")
    push_cmd.add_argument("--limit", type=int, default=5)
    push_cmd.add_argument("--cleanup-after", action="store_true", help="Cancel every hold this run creates before exiting")

    sub.add_parser("cleanup", help="Cancel every temporary_hold currently recorded in id_map")
    sub.add_parser("commit", help="Convert every temporary_hold currently recorded in id_map into a real, permanent reservation")

    args = parser.parse_args()

    _load_env_files()

    if args.command == "check":
        run_check()
        return

    from push import run_push, run_cleanup, run_commit

    if args.command == "push":
        run_push(args.start_time, args.end_time, limit=args.limit, cleanup_after=args.cleanup_after)
    elif args.command == "cleanup":
        run_cleanup()
    elif args.command == "commit":
        run_commit()


if __name__ == "__main__":
    main()
