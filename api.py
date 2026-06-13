import requests

BASE = "https://fantasy.premierleague.com/api"

def get_fixtures():
    r = requests.get(f"{BASE}/fixtures/")
    r.raise_for_status()
    return r.json()

def get_bootstrap():
    """All players, teams, gameweek info"""
    r = requests.get(f"{BASE}/bootstrap-static/")
    r.raise_for_status()
    return r.json()

def get_manager_info(team_id):
    """Manager name, overall rank, total points"""
    r = requests.get(f"{BASE}/entry/{team_id}/")
    r.raise_for_status()
    return r.json()

def get_manager_picks(team_id, gameweek):
    """Manager's 15 players for a specific gameweek"""
    r = requests.get(f"{BASE}/entry/{team_id}/event/{gameweek}/picks/")
    r.raise_for_status()
    return r.json()

def get_current_gameweek(bootstrap):
    """Find the current active gameweek from bootstrap data"""
    for event in bootstrap["events"]:
        if event["is_current"]:
            return event["id"]
    # fallback: last finished
    for event in reversed(bootstrap["events"]):
        if event["finished"]:
            return event["id"]
    return 1