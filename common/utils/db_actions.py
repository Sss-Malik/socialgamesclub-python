from db import fetch_all, fetch_one, execute_query

def get_backend(backend_name):
    query = "SELECT * FROM backend_games WHERE name = %s"
    params = (backend_name,)
    game = fetch_one(query, params)

    if not game:
        raise Exception(f"Backend '{backend_name}' not found")

    return game