from pathlib import Path

def save_credentials(account_id, password, logger, save_dir):
    path = Path(save_dir) / "created_players.txt"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{account_id}:{password}\n")
        logger.info("Saved account: %s", path)
    except Exception as e:
        logger.exception("Failed to save account: %s", e)