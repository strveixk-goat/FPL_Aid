import tomllib
import streamlit as st
from openai import OpenAI

with open("./secrets.toml", "rb") as toml_file:
    secrets = tomllib.load(toml_file)

OPENAI_API_KEY = secrets["apikey"]

def get_next_fixture_difficulty(player_team_id, fixtures, num_fixtures=3):
    """Returns average FDR for a team's next N fixtures"""
    upcoming = [
        f for f in fixtures
        if not f["finished"] and (f["team_h"] == player_team_id or f["team_a"] == player_team_id)
    ]
    upcoming = sorted(upcoming, key=lambda x: x["event"] or 999)[:num_fixtures]

    if not upcoming:
        return 3.0  # neutral fallback

    difficulties = []
    for f in upcoming:
        if f["team_h"] == player_team_id:
            difficulties.append(f["team_h_difficulty"])
        else:
            difficulties.append(f["team_a_difficulty"])

    return sum(difficulties) / len(difficulties)

def is_home(player_team_id, fixtures):
    """Check if player's next fixture is at home"""
    upcoming = [
        f for f in fixtures
        if not f["finished"] and (f["team_h"] == player_team_id or f["team_a"] == player_team_id)
    ]
    upcoming = sorted(upcoming, key=lambda x: x["event"] or 999)
    if not upcoming:
        return False
    return upcoming[0]["team_h"] == player_team_id

def score_player(player, fixtures):
    """Score a player for captaincy using weighted formula"""
    form        = float(player.get("form", 0) or 0)
    ownership   = float(player.get("selected_by_percent", 0) or 0)
    fdr_avg     = get_next_fixture_difficulty(player["team"], fixtures)
    fdr_score   = 5 - fdr_avg  # invert: easier fixture = higher score
    home_bonus  = 0.5 if is_home(player["team"], fixtures) else 0

    score = (
        form       * 0.40 +
        fdr_score  * 0.30 +
        home_bonus * 0.15 +
        (ownership / 100) * 10 * 0.15
    )
    return round(score, 2)

def get_captain_recommendations(picks, players, fixtures, pos_map, top_n=3):
    """Return top N captain picks from starting XI"""
    starters = [p for p in picks["picks"] if p["position"] <= 11]

    scored = []
    for pick in starters:
        p = players[pick["element"]]
        score = score_player(p, fixtures)
        scored.append({
            "name":      p["web_name"],
            "pos":       pos_map[p["element_type"]],
            "team":      p["team"],
            "price":     f"£{p['now_cost'] / 10:.1f}m",
            "form":      p["form"],
            "total_pts": p["total_points"],
            "ownership": p["selected_by_percent"],
            "score":     score,
            "is_captain": pick["is_captain"],
            "fdr":       round(get_next_fixture_difficulty(p["team"], fixtures), 1),
            "home":      is_home(p["team"], fixtures),
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_n]

def get_transfer_recommendations(picks, players, fixtures, pos_map, bank, top_n=3):
    """Find best available transfers across the whole squad"""
    current_ids = {p["element"] for p in picks["picks"]}
    suggestions = []

    for pick in picks["picks"]:
        out_player = players[pick["element"]]
        out_score  = score_player(out_player, fixtures)
        out_price  = out_player["now_cost"] / 10.0
        max_budget = out_price + bank

        # Find best replacement of same position within budget
        candidates = [
            p for p in players.values()
            if p["element_type"] == out_player["element_type"]
            and p["id"] not in current_ids
            and p["now_cost"] / 10.0 <= max_budget
        ]

        for candidate in candidates:
            in_score = score_player(candidate, fixtures)
            gain     = in_score - out_score
            if gain > 0:
                suggestions.append({
                    "out_name":  out_player["web_name"],
                    "out_price": f"£{out_price:.1f}m",
                    "out_score": out_score,
                    "in_name":   candidate["web_name"],
                    "in_price":  f"£{candidate['now_cost'] / 10:.1f}m",
                    "in_score":  in_score,
                    "gain":      round(gain, 2),
                    "pos":       pos_map[candidate["element_type"]],
                })

    suggestions.sort(key=lambda x: x["gain"], reverse=True)
    return suggestions[:top_n]

def get_ai_summary(captain_recs, transfer_recs):
    client = OpenAI(api_key=OPENAI_API_KEY)

    captain_text = "\n".join([
        f"- {r['name']} ({r['pos']}, form {r['form']}, FDR {r['fdr']}, {'home' if r['home'] else 'away'}, score {r['score']})"
        for r in captain_recs
    ])

    transfer_text = "\n".join([
        f"- Transfer OUT {r['out_name']} → IN {r['in_name']} ({r['pos']}, score gain +{r['gain']})"
        for r in transfer_recs
    ]) if transfer_recs else "No strong transfer improvements found."

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are an FPL (Fantasy Premier League) assistant. Be concise, confident, and sound like an FPL pundit. Do NOT use bullet points."},
            {"role": "user", "content": f"""Write a 3-4 sentence summary explaining the top captain pick and best transfer suggestion based on this data:

Captain recommendations (ranked):
{captain_text}

Transfer recommendations (ranked):
{transfer_text}
"""}
        ]
    )
    return response.choices[0].message.content