#!/usr/bin/env python3
import sys
import pkgutil
import importlib
from pathlib import Path
import argparse

def list_backends() -> None:
    """
    List all subfolders under backends/ that contain an __init__.py file.
    """
    base = Path(__file__).parent / "backends"
    print("Available backends:")
    for finder, name, ispkg in pkgutil.iter_modules([str(base)]):
        print(f"  - {name}")


def main():
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="casino_automation: run a specific action for a given backend.",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    # If no arguments are given at all, display help and exit
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    # --list / -l  (no backend needed in this case)
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List all available backends and exit."
    )

    # Positional ‘backend’ is now optional—but will be required unless --list was passed
    parser.add_argument(
        "backend",
        nargs="?",
        help="Name of the backend to invoke (must match a subfolder under backends/)."
    )

    # --action / -a  (required if a backend is specified)
    parser.add_argument(
        "--action", "-a",
        help=(
            "Action to perform for the given backend.\n"
            "Examples:\n"
            "  create-account\n"
            "  account-topup\n"
            "(Hyphens map to underscores internally.)"
        )
    )

    # --count / -c (only used when --action is present)
    parser.add_argument(
        "--count", "-c",
        type=int,
        default=1,
        help="Number of times to perform the chosen action (default: 1)."
    )

    args = parser.parse_args()

    # If user only wants to list backends:
    if args.list:
        list_backends()
        sys.exit(0)

    # Otherwise, require a backend name:
    if not args.backend:
        parser.error("the following arguments are required: backend (when not using --list)")

    # Require --action whenever a backend is given:
    if not args.action:
        parser.error("the --action argument is required when specifying a backend")

    backend_name = args.backend
    module_path = f"backends.{backend_name}.automation"

    try:
        backend_module = importlib.import_module(module_path)
    except ImportError:
        print(f"Error: cannot import backend '{backend_name}'. "
              f"Make sure it exists under backends/ directory.")
        sys.exit(1)

    # Map hyphens in action → underscores in function name
    sanitized = args.action.replace("-", "_")
    func_name = f"action_{sanitized}"

    if not hasattr(backend_module, func_name):
        print(f"Error: backend '{backend_name}' has no action '{args.action}'.\n"
              f"Expected function '{func_name}' in {module_path}.")
        sys.exit(1)

    action_func = getattr(backend_module, func_name)

    count = args.count
    if count < 1:
        print("Error: --count must be >= 1.")
        sys.exit(1)

    print(f"Running action '{args.action}' (count={count}) for backend: {backend_name}\n")
    try:
        action_func(count)
    except TypeError:
        print(f"Error: '{func_name}' does not accept a single integer argument.")
        sys.exit(1)


if __name__ == "__main__":
    main()
