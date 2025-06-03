
import sys
import pkgutil
import importlib
from pathlib import Path

def list_backends() -> None:
    """
    List all subfolders under backends/ that contain an __init__.py file.
    """
    base = Path(__file__).parent / "backends"
    print("Available backends:")
    for finder, name, ispkg in pkgutil.iter_modules([str(base)]):
        # We assume that each subdirectory in backends/ is a backend package
        print(f"  - {name}")

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print("Usage:")
        print("  python main.py <backend_name>   # run the automation for that backend")
        print("  python main.py --list           # list available backends")
        sys.exit(0)

    if sys.argv[1] in ("--list", "-l"):
        list_backends()
        sys.exit(0)

    backend_name = sys.argv[1]
    # Dynamically import backends.<backend_name>.automation and call run()
    module_path = f"backends.{backend_name}.automation"
    try:
        backend_module = importlib.import_module(module_path)
    except ImportError as e:
        print(f"Error: cannot import backend '{backend_name}'. Make sure it exists in backends/ directory.")
        sys.exit(1)

    if not hasattr(backend_module, "run"):
        print(f"Error: module '{module_path}' has no function 'run()'.")
        sys.exit(1)

    print(f"Launching automation for backend: {backend_name}\n")
    backend_module.run()


if __name__ == "__main__":
    main()
