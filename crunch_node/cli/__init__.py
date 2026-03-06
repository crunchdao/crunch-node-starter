"""crunch-node CLI — workspace scaffolding and management."""

import argparse
import sys

from crunch_node.cli.scaffold import list_packs, scaffold_workspace


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="crunch-node",
        description="Coordinator node management CLI",
    )
    subparsers = parser.add_subparsers(dest="command")

    # ── init ───────────────────────────────────────────────────────────
    init_parser = subparsers.add_parser(
        "init",
        help="Create a new competition workspace from scaffold template",
    )
    init_parser.add_argument(
        "name",
        help=(
            "Competition name in kebab-case (e.g. 'my-btc-challenge'). "
            "Used as directory name and CRUNCH_ID."
        ),
    )
    init_parser.add_argument(
        "--pack",
        default=None,
        help="Competition pack to apply on top of scaffold base",
    )
    init_parser.add_argument(
        "--output-dir",
        "-o",
        default=".",
        help="Parent directory for the workspace (default: current directory)",
    )
    init_parser.add_argument(
        "--no-webapp-clone",
        action="store_true",
        help=argparse.SUPPRESS,
    )

    # ── list-packs ─────────────────────────────────────────────────────
    subparsers.add_parser(
        "list-packs",
        help="List available competition packs",
    )

    args = parser.parse_args()

    if args.command == "init":
        try:
            scaffold_workspace(
                name=args.name,
                pack=args.pack,
                output_dir=args.output_dir,
                clone_webapp=not args.no_webapp_clone,
            )
        except (FileExistsError, FileNotFoundError, ValueError, RuntimeError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "list-packs":
        list_packs()
    else:
        parser.print_help()
        sys.exit(1)
