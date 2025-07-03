#!/usr/bin/env python3
import sys
import pkgutil
import importlib
from pathlib import Path
import argparse
import inspect
import logging
from datetime import datetime

def list_backends() -> None:
    """
    List all subfolders under backends/ that contain an __init__.py file.
    """
    base = Path(__file__).parent / "backends"
    print("Available backends:")
    for finder, name, ispkg in pkgutil.iter_modules([str(base)]):
        print(f"  - {name}")


def parse_arguments():
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description="casino_automation: run a specific action for a given backend.",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    parser.add_argument("--list", "-l", action="store_true", help="List all available backends and exit.")
    parser.add_argument("backend", nargs="?",
                        help="Name of the backend to invoke (must match a subfolder under backends/).")
    parser.add_argument("--action", "-a", help="Action to perform for the given backend. Hyphens map to underscores.")
    parser.add_argument("--count", "-c", type=int, default=1,
                        help="Number of times to perform the action (default: 1).")
    parser.add_argument("--account", help="Account ID used by certain actions like recharge-account.")

    return parser.parse_args(), parser


def main():
    args, parser = parse_arguments()

    if args.list:
        list_backends()
        sys.exit(0)

    if not args.backend:
        parser.error("the following arguments are required: backend (when not using --list)")
    if not args.action:
        parser.error("the --action argument is required when specifying a backend")

    backend_name = args.backend
    module_path = f"backends.{backend_name}.automation"
    try:
        backend_module = importlib.import_module(module_path)
    except ImportError:
        print(f"Error: cannot import backend '{backend_name}'. Make sure it exists under backends/.")
        sys.exit(1)

    action_name = args.action.replace("-", "_")
    func_name = f"action_{action_name}"

    if not hasattr(backend_module, func_name):
        print(f"Error: backend '{backend_name}' has no action '{args.action}'.\n"
                     f"Expected function '{func_name}' in {module_path}.")
        sys.exit(1)

    action_func = getattr(backend_module, func_name)

    # Introspect function signature
    sig = inspect.signature(action_func)
    kwargs = {}

    for name, param in sig.parameters.items():
        if name == "page":
            continue
        if name == "count":
            kwargs["count"] = args.count
        elif name == "account_id":
            if args.account is None:
                print("Error: --account is required for this action.")
                sys.exit(1)
            kwargs["account_id"] = args.account
        else:
            print(f"Error: Unsupported parameter '{name}' in function '{func_name}'.")
            sys.exit(1)

    print(f"Running action '{args.action}' with args {kwargs} for backend '{backend_name}'")

    try:
        action_func(**kwargs)
    except Exception as e:
        print(f"Error while executing action: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
