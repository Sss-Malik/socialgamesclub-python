def ensure_directories(*paths):
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)