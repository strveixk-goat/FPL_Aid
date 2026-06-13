import streamlit as st
import hashlib
import sqlite3
import pandas as pd
import copy
import requests
import altair as alt

# ── DB setup ──────────────────────────────────────────────
DB_PATH = "fpl_helper.db"
BASE = "https://fantasy.premierleague.com/api"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS managers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            fpl_team_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER NOT NULL,
            gameweek INTEGER,
            total_points INTEGER,
            overall_rank INTEGER,
            saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (manager_id) REFERENCES managers(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS squad_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            player_fpl_id INTEGER NOT NULL,
            player_name TEXT,
            position TEXT,
            is_starter INTEGER DEFAULT 1,
            is_captain INTEGER DEFAULT 0,
            FOREIGN KEY (session_id) REFERENCES sessions(id)
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manager_id INTEGER,
            player_out_name TEXT,
            player_out_price REAL,
            player_in_name TEXT,
            player_in_price REAL,
            gameweek INTEGER,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def db_create_account(username, password):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT id FROM managers WHERE username = ?", (username,))
    if c.fetchone():
        conn.close()
        return False, "Username already taken."
    c.execute(
        "INSERT INTO managers (username, password) VALUES (?, ?)",
        (username, hash_password(password))
    )
    conn.commit()
    conn.close()
    return True, "Account created! You can now log in."

def db_login(username, password):
    conn = get_connection()
    c = conn.cursor()
    c.execute(
        "SELECT * FROM managers WHERE username = ? AND password = ?",
        (username, hash_password(password))
    )
    user = c.fetchone()
    conn.close()
    return user

def get_manager_history(team_id):
    r = requests.get(f"{BASE}/entry/{team_id}/history/")
    r.raise_for_status()
    return r.json()

def db_execute_transfer(manager_id, gw, player_out_name, player_out_price, player_in_name, player_in_price):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        INSERT INTO transfers (manager_id, player_out_name, player_out_price, player_in_name, player_in_price, gameweek)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (manager_id, player_out_name, player_out_price, player_in_name, player_in_price, gw))
    conn.commit()
    conn.close()

def db_get_gameweek_transfers(manager_id, gw):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM transfers WHERE manager_id = ? AND gameweek = ?", (manager_id, gw))
    rows = c.fetchall()
    conn.close()
    return rows

def db_get_all_transfers_cost_modifier(manager_id):
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT SUM(player_in_price - player_out_price) as net_spent FROM transfers WHERE manager_id = ?", (manager_id,))
    row = c.fetchone()
    conn.close()
    return row["net_spent"] if row["net_spent"] else 0.0

def db_reset_gameweek_transfers(manager_id, gw):
    conn = get_connection()
    c = conn.cursor()
    c.execute("DELETE FROM transfers WHERE manager_id = ? AND gameweek = ?", (manager_id, gw))
    conn.commit()
    conn.close()


# ── Page config ───────────────────────────────────────────
st.set_page_config(page_title="FPL Helper", page_icon="⚽", layout="wide")

# ── Session state defaults ────────────────────────────────
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = None
if "manager_id" not in st.session_state:
    st.session_state.manager_id = None
if "transfer_out" not in st.session_state:
    st.session_state.transfer_out = None
if "transfer_in" not in st.session_state:
    st.session_state.transfer_in = None
if "transfer_limit" not in st.session_state:
    st.session_state.transfer_limit = 2
if "transfer_step" not in st.session_state:
    st.session_state.transfer_step = "idle"
if "swap_p1" not in st.session_state:
    st.session_state.swap_p1 = None

init_db()

# ── Auth pages ────────────────────────────────────────────
def show_login():
    st.title("⚽ FPL Helper")
    tab1, tab2 = st.tabs(["Login", "Create Account"])

    with tab1:
        st.subheader("Login")
        username = st.text_input("Username", key="li_user")
        password = st.text_input("Password", type="password", key="li_pass")
        if st.button("Login", key="li_btn"):
            if not username or not password:
                st.error("Please fill in both fields.")
            else:
                user = db_login(username, password)
                if user:
                    st.session_state.logged_in = True
                    st.session_state.username = username
                    st.session_state.manager_id = user["id"]
                    st.rerun()
                else:
                    st.error("Incorrect username or password.")

    with tab2:
        st.subheader("Create Account")
        new_user = st.text_input("Username", key="ca_user")
        new_pass = st.text_input("Password", type="password", key="ca_pass")
        if st.button("Create Account", key="ca_btn"):
            if not new_user or not new_pass:
                st.error("Please fill in both fields.")
            else:
                success, msg = db_create_account(new_user, new_pass)
                if success:
                    st.success(msg)
                else:
                    st.error(msg)

# ── Feature Components ────────────────────────────────────

def get_player_info(pick, players, pos_map, gw_pts_lookup):
    p = players[pick["element"]]
    return {
        "name": p["web_name"],
        "pos": pos_map.get(p["element_type"], "?"),
        "total_pts": p["total_points"],
        "gw_pts": gw_pts_lookup.get(pick["element"], 0),
        "price": f"£{p['now_cost'] / 10:.1f}m",
        "captain": pick["is_captain"],
        "vice": pick["is_vice_captain"],
        "status": p["status"]
    }

def validate_formation(test_picks, players):
    """Ensures a proposed swap doesn't break FPL formation rules."""
    starters = [p for p in test_picks if p["position"] <= 11]
    counts = {1: 0, 2: 0, 3: 0, 4: 0} # GKP, DEF, MID, FWD
    
    for p in starters:
        el_type = players[p["element"]]["element_type"]
        counts[el_type] += 1
        
    if counts[1] != 1: 
        return False, "You must have exactly 1 starting Goalkeeper."
    if not (3 <= counts[2] <= 5): 
        return False, "You must have between 3 and 5 starting Defenders."
    if not (2 <= counts[3] <= 5): 
        return False, "You must have between 2 and 5 starting Midfielders."
    if not (1 <= counts[4] <= 3): 
        return False, "You must have between 1 and 3 starting Forwards."
    
    return True, "Valid formation"

def show_team_tab(picks, players, pos_map, gw, bootstrap):
    def handle_gw_change():
        new_gw = st.session_state.gw_input_box
        if new_gw != st.session_state.get("gw"):
            try:
                from api import get_manager_picks
                fetched_picks = get_manager_picks(st.session_state.team_id, new_gw)
                try:
                    from api import get_live_data
                    live_data = get_live_data(new_gw)
                except ImportError:
                    import requests
                    res = requests.get(f"https://fantasy.premierleague.com/api/event/{new_gw}/live/")
                    live_data = res.json() if res.status_code == 200 else {}
                
                st.session_state.picks = fetched_picks
                st.session_state.live_data = live_data
                st.session_state.gw = new_gw
                st.session_state.swap_p1 = None # Reset swaps on GW change
            except Exception as e:
                st.session_state.gw_fetch_error = f"No data available for GW{new_gw} yet."

    selected_gw = st.number_input(
        "Gameweek", 
        min_value=1, max_value=38, 
        value=int(st.session_state.get("gw", gw)), 
        step=1, key="gw_input_box", on_change=handle_gw_change
    )

    if "gw_fetch_error" in st.session_state and st.session_state.gw_fetch_error:
        st.error(st.session_state.gw_fetch_error)
        st.session_state.gw_fetch_error = None
        return

    gw_event = next((e for e in bootstrap["events"] if e["id"] == selected_gw), None)
    if gw_event and not gw_event["finished"] and not gw_event["is_current"]:
        st.warning(f"GW{selected_gw} hasn't happened yet — showing predicted data only.")

    if "picks" not in st.session_state or "live_data" not in st.session_state:
        st.session_state.picks = picks
        st.session_state.gw = selected_gw
        if "live_data" not in st.session_state:
            try:
                from api import get_live_data
                st.session_state.live_data = get_live_data(selected_gw)
            except Exception:
                st.session_state.live_data = {}

    current_picks = st.session_state.picks
    starters = sorted([p for p in current_picks["picks"] if p["position"] <= 11], key=lambda x: x["position"])
    bench    = sorted([p for p in current_picks["picks"] if p["position"] > 11],  key=lambda x: x["position"])

    live_elements = st.session_state.get("live_data", {}).get("elements", [])
    base_pts_lookup = {el["id"]: el["stats"]["total_points"] for el in live_elements if "stats" in el}

    gw_pts_lookup = {}
    for p in current_picks["picks"]:
        element_id = p["element"]
        multiplier = p.get("multiplier", 1)
        base_pts = base_pts_lookup.get(element_id, 0)
        active_multiplier = multiplier if multiplier > 0 else 1
        gw_pts_lookup[element_id] = base_pts * active_multiplier

    def generate_player_card_html(pick):
        p = get_player_info(pick, players, pos_map, gw_pts_lookup)
        badge = "©" if p["captain"] else "v" if p["vice"] else ""
        status = "🟢" if p["status"] == "a" else "🟡" if p["status"] == "d" else "🔴"
        badge_html = f'<div style="background:#38ef7d;color:#000;border-radius:4px;font-size:10px;font-weight:700;padding:1px 5px;display:inline-block;margin-bottom:3px;">{badge}</div>' if badge else '<div style="height:18px;"></div>'

        return f"""
        <div style="background: rgba(30, 33, 48, 0.9); border: 1px solid #2e3250; border-radius: 10px; padding: 10px 6px; text-align: center; width: 95px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); flex-shrink: 0; box-sizing: border-box;">
            {badge_html}
            <div style="font-size:12px;font-weight:600;color:#fff;line-height:1.3;white-space: nowrap; overflow: hidden; text-overflow: ellipsis;" title="{p['name']}">{p['name']}</div>
            <div style="font-size:11px;color:#aaa;margin:2px 0;">{p['pos']} · {p['price']}</div>
            <div style="font-size:16px;font-weight:700;color:#38ef7d;">{p['gw_pts']}</div>
            <div style="font-size:10px;color:#666;">{status} {p['total_pts']} total</div>
        </div>
        """

    def generate_row_html(player_list):
        cards_html = "".join([generate_player_card_html(pick) for pick in player_list])
        return f'<div style="display: flex; justify-content: center; gap: 14px; width: 100%; margin-bottom: 12px;">{cards_html}</div>'

    gkp  = [p for p in starters if players[p["element"]]["element_type"] == 1]
    defs = [p for p in starters if players[p["element"]]["element_type"] == 2]
    mids = [p for p in starters if players[p["element"]]["element_type"] == 3]
    fwds = [p for p in starters if players[p["element"]]["element_type"] == 4]

    st.markdown(f"#### GW{selected_gw} Starting XI")
    pitch_html = f"""
    <div style="background: linear-gradient(to bottom, #14451f 0%, #1a5c2b 100%); background-image: linear-gradient(#165225 50%, #12441e 50%); background-size: 100% 70px; padding: 30px 10px; border-radius: 14px; border: 2px solid #226b34; display: flex; flex-direction: column; align-items: center; width: 100%; box-sizing: border-box; box-shadow: inset 0 0 20px rgba(0,0,0,0.4);">
        {generate_row_html(fwds)} {generate_row_html(mids)} {generate_row_html(defs)} {generate_row_html(gkp)}
    </div>
    """
    st.html(pitch_html)

    st.markdown("#### Bench")
    bench_html = f'<div style="background: #161b22; padding: 16px 10px; border-radius: 10px; border: 1px solid #2a2a2a; width: 100%; box-sizing: border-box;">{generate_row_html(bench)}</div>'
    st.html(bench_html)

    # ── Interactive Substitution Manager ───────────────────────────
    st.divider()
    st.markdown("### 🔄 Substitution Manager")
    
    if st.session_state.swap_p1 is None:
        st.info("👇 **Two-Tap Swap:** Click a player below to initiate a substitution.")
    else:
        p1_name = players[st.session_state.swap_p1["element"]]["web_name"]
        st.warning(f"🔄 **Swapping {p1_name}** — Click another player below to swap them, or click {p1_name} again to cancel.")

    def execute_swap(p1_pick, p2_pick):
        test_picks = copy.deepcopy(st.session_state.picks["picks"])
        
        t_p1 = next(p for p in test_picks if p["element"] == p1_pick["element"])
        t_p2 = next(p for p in test_picks if p["element"] == p2_pick["element"])
        
        # Safely swap their slots completely (position, points multiplier, and captaincy)
        for key in ["position", "multiplier", "is_captain", "is_vice_captain"]:
            t_p1[key], t_p2[key] = t_p2[key], t_p1[key]
            
        is_valid, msg = validate_formation(test_picks, players)
        if is_valid:
            st.session_state.picks["picks"] = test_picks
            st.session_state.swap_p1 = None
            st.toast(f"✅ Subbed successfully!")
        else:
            st.error(f"🚫 Invalid Formation: {msg}")
            st.session_state.swap_p1 = None

    def render_sub_row(player_picks, label):
        if not player_picks: return
        st.caption(label)
        cols = st.columns(len(player_picks))
        for i, pick in enumerate(player_picks):
            pid = pick["element"]
            p_info = players[pid]
            name = p_info["web_name"]
            pos = pos_map[p_info["element_type"]]
            
            is_selected = (st.session_state.swap_p1 is not None and st.session_state.swap_p1["element"] == pid)
            btn_type = "primary" if is_selected else "secondary"
            
            with cols[i]:
                if st.button(f"{name}\n({pos})", key=f"sub_{pid}", use_container_width=True, type=btn_type):
                    if st.session_state.swap_p1 is None:
                        st.session_state.swap_p1 = pick
                    elif st.session_state.swap_p1["element"] == pid:
                        st.session_state.swap_p1 = None # Cancel click
                    else:
                        execute_swap(st.session_state.swap_p1, pick)
                    st.rerun()

    # Re-fetch fresh arrays specifically for the buttons to ensure accuracy
    starters_fresh = sorted([p for p in current_picks["picks"] if p["position"] <= 11], key=lambda x: players[x["element"]]["element_type"])
    bench_fresh    = sorted([p for p in current_picks["picks"] if p["position"] > 11],  key=lambda x: x["position"])

    gkp_s  = [p for p in starters_fresh if players[p["element"]]["element_type"] == 1]
    defs_s = [p for p in starters_fresh if players[p["element"]]["element_type"] == 2]
    mids_s = [p for p in starters_fresh if players[p["element"]]["element_type"] == 3]
    fwds_s = [p for p in starters_fresh if players[p["element"]]["element_type"] == 4]

    with st.container(border=True):
        render_sub_row(gkp_s, "Goalkeeper")
        render_sub_row(defs_s, "Defenders")
        render_sub_row(mids_s, "Midfielders")
        render_sub_row(fwds_s, "Forwards")
        st.markdown("---")
        render_sub_row(bench_fresh, "Bench (Click to sub on)")


def show_transfers_tab(bootstrap, players, pos_map, current_gw_balance):
    st.subheader("Transfer Market")
    teams_map = {t["id"]: t["name"] for t in bootstrap["teams"]}
    active_gw = st.session_state.get("gw", 1)

    completed_transfers = db_get_gameweek_transfers(st.session_state.manager_id, active_gw)
    transfers_remaining = max(0, st.session_state.transfer_limit - len(completed_transfers))
    
    st.info(f"📋 **Weekly Transfer Limit:** {len(completed_transfers)} / {st.session_state.transfer_limit} transfers used this week. (**{transfers_remaining} remaining**)")

    # ── Wizard State Management ─────────────────────────────────────
    
    # STEP 1: IDLE STATE
    if st.session_state.transfer_step == "idle":
        if transfers_remaining <= 0:
            st.error("🚫 You have reached your weekly transfer limit. Reset the simulation in the sidebar if you wish to make more.")
        else:
            if st.button("🔄 Initiate New Transfer", type="primary", use_container_width=True):
                st.session_state.transfer_step = "select_out"
                st.rerun()

    # STEP 2: SELECT SQUAD PLAYER TO LEAVE
    elif st.session_state.transfer_step == "select_out":
        st.markdown("### Step 1: Select a player to transfer **OUT**")
        
        current_team_ids = [pick["element"] for pick in st.session_state.picks.get("picks", [])]
        
        squad_by_pos = {"GKP": [], "DEF": [], "MID": [], "FWD": []}
        for pid in current_team_ids:
            p = players[pid]
            squad_by_pos[pos_map[p["element_type"]]].append(p)
            
        for pos_name, pos_players in squad_by_pos.items():
            st.markdown(f"**{pos_name}s**")
            cols = st.columns(len(pos_players))
            for i, p in enumerate(pos_players):
                with cols[i]:
                    if st.button(f"{p['web_name']} \n(£{p['now_cost']/10:.1f}m)", key=f"out_{p['id']}", use_container_width=True):
                        st.session_state.transfer_out = {
                            "id": p["id"], 
                            "name": p["web_name"], 
                            "price": p["now_cost"] / 10.0,
                            "pos_id": p["element_type"]
                        }
                        st.session_state.transfer_step = "select_in"
                        st.rerun()
        
        st.divider()
        if st.button("Cancel Transfer"):
            st.session_state.transfer_step = "idle"
            st.rerun()

    # STEP 3: SELECT MARKET PLAYER TO JOIN
    elif st.session_state.transfer_step == "select_in":
        out_p = st.session_state.transfer_out
        max_affordable = current_gw_balance + out_p["price"]
        
        st.markdown("### Step 2: Select a replacement to bring **IN**")
        st.warning(f"🔴 Leaving: **{out_p['name']}** (£{out_p['price']:.1f}m)")
        st.success(f"💰 Max Budget: **£{max_affordable:.1f}m** (Must be a {pos_map[out_p['pos_id']]})")
        
        if st.button("Cancel Transfer"):
            st.session_state.transfer_step = "idle"
            st.session_state.transfer_out = None
            st.rerun()

        current_team_ids = [pick["element"] for pick in st.session_state.picks.get("picks", [])]
        market_data = []
        for p in players.values():
            if p["element_type"] == out_p["pos_id"] and p["id"] not in current_team_ids:
                market_data.append({
                    "ID": p["id"],
                    "Name": p["web_name"],
                    "Team": teams_map.get(p["team"], "Unknown"),
                    "Price": p["now_cost"] / 10.0,
                    "Total Pts": p["total_points"],
                    "Form": float(p["form"]),
                    "Selected %": float(p["selected_by_percent"])
                })
        
        df = pd.DataFrame(market_data)
        
        col1, _ = st.columns([1, 1])
        with col1:
            price_filter = st.slider("Filter by Max Price", min_value=4.0, max_value=float(max_affordable), value=float(max_affordable), step=0.1)
        
        filtered_df = df[df["Price"] <= price_filter].sort_values(by="Total Pts", ascending=False).reset_index(drop=True)
        
        st.markdown("👉 **Click any row to select that player.** (Click column headers to sort)")
        
        event = st.dataframe(
            filtered_df[["Name", "Team", "Price", "Total Pts", "Form", "Selected %"]],
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row"
        )
        
        selected_rows = event.selection.rows
        if selected_rows:
            selected_index = selected_rows[0]
            selected_player_id = filtered_df.iloc[selected_index]["ID"]
            in_p = players[selected_player_id]
            
            st.session_state.transfer_in = {
                "id": in_p["id"], 
                "name": in_p["web_name"], 
                "price": in_p["now_cost"] / 10.0
            }
            st.session_state.transfer_step = "confirm"
            st.rerun()


    # STEP 4: CONFIRM TRANSACTION
    elif st.session_state.transfer_step == "confirm":
        st.markdown("### Step 3: Confirm Transaction")
        
        out_p = st.session_state.transfer_out
        in_p = st.session_state.transfer_in
        cost_diff = in_p['price'] - out_p['price']
        projected_bank = current_gw_balance - cost_diff
        
        with st.container(border=True):
            c1, c2, c3 = st.columns(3)
            c1.error(f"🔴 OUT: **{out_p['name']}**\n\n(£{out_p['price']}m)")
            c2.success(f"🟢 IN: **{in_p['name']}**\n\n(£{in_p['price']}m)")
            c3.metric("Projected Remaining Bank", f"£{projected_bank:.1f}m", delta=f"{-cost_diff:+.1f}m")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Cancel Transfer"):
                st.session_state.transfer_step = "idle"
                st.session_state.transfer_out = None
                st.session_state.transfer_in = None
                st.rerun()
        with col2:
            if st.button("✅ Confirm Transfer", type="primary", use_container_width=True):
                db_execute_transfer(
                    manager_id=st.session_state.manager_id,
                    gw=active_gw,
                    player_out_name=out_p['name'],
                    player_out_price=out_p['price'],
                    player_in_name=in_p['name'],
                    player_in_price=in_p['price']
                )
                
                cpicks = st.session_state.picks.copy()
                for p in cpicks["picks"]:
                    if p["element"] == out_p['id']:
                        p["element"] = in_p['id']
                
                st.session_state.picks = cpicks
                st.toast(f"Transferred {out_p['name']} ➡️ {in_p['name']}!")
                
                st.session_state.transfer_step = "idle"
                st.session_state.transfer_out = None
                st.session_state.transfer_in = None
                st.rerun()

def show_history_tab(manager, team_id):
    if "history" not in st.session_state:
        with st.spinner("Loading season history..."):
            try:
                st.session_state.history = get_manager_history(team_id)
            except Exception as e:
                st.error(f"Couldn't load history: {e}")
                return

    history = st.session_state.history
    gw_history = history.get("current", [])

    if not gw_history:
        st.info("No gameweek history available yet.")
        return

    df = pd.DataFrame(gw_history)

    # ── Season stat cards ──
    best_gw     = df.loc[df["points"].idxmax()]
    worst_gw    = df.loc[df["points"].idxmin()]
    avg_pts     = round(df["points"].mean(), 1)
    total_pts   = df["points"].sum()
    total_hits  = df["event_transfers_cost"].sum()
    total_trans = df["event_transfers"].sum()

    st.markdown("### Season Summary")
    c1, c2, c3 = st.columns(3)
    c1.metric("Total Points",      total_pts)
    c2.metric("Average GW Points", avg_pts)
    c3.metric("Total Transfer Hits", f"-{total_hits} pts")

    c4, c5, c6 = st.columns(3)
    c4.metric("Best GW",  f"GW{int(best_gw['event'])}",  f"{int(best_gw['points'])} pts")
    c5.metric("Worst GW", f"GW{int(worst_gw['event'])}", f"{int(worst_gw['points'])} pts")
    c6.metric("Total Transfers Made", total_trans)

    st.divider()

    # ── Points per GW bar + line ──
    st.markdown("### Points Per Gameweek")

    points_chart = alt.Chart(df).mark_bar(color="#38ef7d", opacity=0.8).encode(
        x=alt.X("event:O", title="Gameweek"),
        y=alt.Y("points:Q", title="Points"),
        tooltip=["event", "points", "total_points"]
    ).properties(height=280)

    avg_line = alt.Chart(pd.DataFrame({"avg": [avg_pts]})).mark_rule(
        color="#ef5757", strokeDash=[4, 4], size=1.5
    ).encode(y="avg:Q")

    st.altair_chart(points_chart + avg_line, use_container_width=True)

    # ── Rank progression ──
    st.markdown("### Overall Rank Progression")

    rank_chart = alt.Chart(df).mark_line(
        color="#7c9fff", point=alt.OverlayMarkDef(color="#7c9fff", size=60)
    ).encode(
        x=alt.X("event:O", title="Gameweek"),
        y=alt.Y("overall_rank:Q", title="Overall Rank", scale=alt.Scale(reverse=True)),
        tooltip=["event", "overall_rank", "points"]
    ).properties(height=280)

    st.altair_chart(rank_chart, use_container_width=True)
    st.caption("↑ Higher on chart = better rank")

    # ── GW breakdown table ──
    st.markdown("### Gameweek Breakdown")
    display_df = df[["event", "points", "total_points", "overall_rank", "event_transfers", "event_transfers_cost"]].copy()
    display_df.columns = ["GW", "GW Pts", "Total Pts", "Overall Rank", "Transfers", "Hit Cost"]
    display_df["Overall Rank"] = display_df["Overall Rank"].apply(lambda x: f"{x:,}")
    st.dataframe(display_df, use_container_width=True, hide_index=True)

    # ── Chip history ──
    chips = history.get("chips", [])
    if chips:
        st.markdown("### Chips Used")
        for chip in chips:
            st.markdown(f"- **{chip['name'].replace('_', ' ').title()}** — GW{chip['event']}")

def show_dashboard():
    def handle_limit_change():
        st.session_state.transfer_limit = st.session_state.limit_input_box

    if "gw" not in st.session_state and "bootstrap" in st.session_state:
        from api import get_current_gameweek
        st.session_state.gw = get_current_gameweek(st.session_state.bootstrap)

    with st.sidebar:
        st.markdown("## ⚽ FPL Helper")
        st.write(f"👋 **{st.session_state.username}**")
        st.divider()
        
        st.number_input(
            "Set Weekly Transfer Limit", 
            min_value=1, 
            max_value=5, 
            value=int(st.session_state.get("transfer_limit", 2)),
            key="limit_input_box",
            on_change=handle_limit_change
        )
        
        if "manager" in st.session_state and "gw" in st.session_state:
            active_gw = st.session_state.gw
            if st.button("🔄 Reset Week's Simulation", use_container_width=True):
                db_reset_gameweek_transfers(st.session_state.manager_id, active_gw)
                
                if "team_id" in st.session_state:
                    with st.spinner("Re-syncing with official FPL..."):
                        try:
                            from api import get_manager_picks
                            picks = get_manager_picks(st.session_state.team_id, active_gw)
                            st.session_state.picks = picks
                        except Exception as e:
                            st.error(f"Failed to re-fetch official squad: {e}")
                
                st.session_state.transfer_step = "idle"
                st.session_state.transfer_out = None
                st.session_state.transfer_in = None
                st.session_state.swap_p1 = None
                st.toast("Weekly transfer limit and squad reset to match official FPL!")
                st.rerun()

        st.divider()
        team_id = st.text_input("FPL Team ID", placeholder="e.g. 1234567")
        if st.button("Load Team"):
            if not team_id:
                st.error("Enter a Team ID first.")
            else:
                with st.spinner("Fetching your team..."):
                    try:
                        from api import get_bootstrap, get_manager_info, get_manager_picks, get_current_gameweek
                        bootstrap = get_bootstrap()
                        manager   = get_manager_info(team_id)
                        gw        = get_current_gameweek(bootstrap)
                        picks     = get_manager_picks(team_id, gw)
                        
                        try:
                            from api import get_live_data
                            live_data = get_live_data(gw)
                        except ImportError:
                            import requests
                            res = requests.get(f"https://fantasy.premierleague.com/api/event/{gw}/live/")
                            live_data = res.json() if res.status_code == 200 else {}

                        st.session_state.bootstrap  = bootstrap
                        st.session_state.manager    = manager
                        st.session_state.picks      = picks
                        st.session_state.live_data  = live_data
                        st.session_state.gw         = gw
                        st.session_state.team_id    = team_id
                        st.session_state.transfer_step = "idle"
                        st.session_state.swap_p1 = None
                        st.rerun() 
                    except Exception as e:
                        st.error(f"Couldn't load team. Check your Team ID.\n\n{e}")
                        
        st.divider()
        if st.button("Logout"):
            st.session_state.logged_in = False
            st.session_state.username  = None
            st.session_state.manager_id = None
            st.session_state.transfer_out = None
            st.session_state.transfer_in = None
            st.session_state.transfer_step = "idle"
            st.session_state.swap_p1 = None
            if "gw" in st.session_state: del st.session_state.gw
            if "manager" in st.session_state: del st.session_state.manager
            st.rerun()

    if "manager" not in st.session_state:
        st.title("Dashboard")
        st.info("Enter your FPL Team ID in the sidebar and click **Load Team**.")
        return

    manager   = st.session_state.manager
    picks     = st.session_state.picks
    bootstrap = st.session_state.bootstrap
    gw        = st.session_state.gw
    players   = {p["id"]: p for p in bootstrap["elements"]}
    pos_map   = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

    api_initial_bank = manager["last_deadline_bank"] / 10.0
    net_modifiers = db_get_all_transfers_cost_modifier(st.session_state.manager_id)
    live_bank_balance = api_initial_bank - net_modifiers

    st.title(f"⚽ {manager['name']}")

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Points",  manager["summary_overall_points"])
    col2.metric("Overall Rank",  f"{manager['summary_overall_rank']:,}")
    col3.metric(f"GW{gw} Points", manager["summary_event_points"])
    col4.metric("Bank Available", f"£{live_bank_balance:.1f}m")

    st.divider()

    tab_dash, tab_team, tab_transfers, tab_history = st.tabs(["Dashboard", "Team", "Transfers", "History"])

    with tab_dash:
        from recommendations import get_captain_recommendations, get_transfer_recommendations, get_ai_summary
        from api import get_fixtures

        if "fixtures" not in st.session_state:
            with st.spinner("Loading fixtures..."):
                st.session_state.fixtures = get_fixtures()

        fixtures = st.session_state.fixtures
        captain_recs  = get_captain_recommendations(picks, players, fixtures, pos_map)
        transfer_recs = get_transfer_recommendations(picks, players, fixtures, pos_map, live_bank_balance)

        st.markdown("### Recommendations")

        # ── Captain picks ──
        st.markdown("#### ⭐ Captain")
        for i, r in enumerate(captain_recs):
            medal = ["🥇", "🥈", "🥉"][i]
            current = " *(current captain)*" if r["is_captain"] else ""
            st.markdown(
                f"""
                <div style="background:#1e2130;border:1px solid #2e3250;border-radius:10px;
                    padding:14px 18px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;">
                    <div>
                        <span style="font-size:20px;">{medal}</span>
                        <span style="font-size:15px;font-weight:600;color:#fff;margin-left:8px;">{r['name']}</span>
                        <span style="font-size:12px;color:#aaa;margin-left:6px;">{r['pos']} · {r['price']}</span>
                        <span style="font-size:11px;color:#38ef7d;margin-left:6px;">{current}</span>
                    </div>
                    <div style="text-align:right;">
                        <div style="font-size:13px;color:#aaa;">Form <b style="color:#fff">{r['form']}</b> · 
                            FDR <b style="color:#fff">{r['fdr']}</b> · 
                            {'🏠 Home' if r['home'] else '✈️ Away'}</div>
                        <div style="font-size:18px;font-weight:700;color:#38ef7d;">Score {r['score']}</div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )

        # ── Transfer picks ──
        st.markdown("#### 🔄 Transfer")
        if not transfer_recs:
            st.success("Your squad looks strong — no major transfer improvements found!")
        else:
            for i, r in enumerate(transfer_recs):
                medal = ["🥇", "🥈", "🥉"][i]
                st.markdown(
                    f"""
                    <div style="background:#1e2130;border:1px solid #2e3250;border-radius:10px;
                        padding:14px 18px;margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;">
                        <div>
                            <span style="font-size:20px;">{medal}</span>
                            <span style="font-size:13px;color:#ef5757;margin-left:8px;">OUT {r['out_name']} {r['out_price']}</span>
                            <span style="font-size:15px;color:#aaa;margin:0 6px;">→</span>
                            <span style="font-size:13px;color:#38ef7d;">IN {r['in_name']} {r['in_price']}</span>
                            <span style="font-size:11px;color:#aaa;margin-left:6px;">({r['pos']})</span>
                        </div>
                        <div style="font-size:16px;font-weight:700;color:#38ef7d;">+{r['gain']} gain</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )

        # ── AI Summary ──
        st.markdown("#### 🤖 AI Summary")
        if st.button("Generate AI Summary", type="primary"):
            with st.spinner("Asking OpenAI..."):
                try:
                    summary = get_ai_summary(captain_recs, transfer_recs)
                    st.session_state.ai_summary = summary
                except Exception as e:
                    st.error(f"OpenAI error: {e}")

        if "ai_summary" in st.session_state:
            st.markdown(
                f"""
                <div style="background:#1a1a2e;border:1px solid #2a2a4a;border-radius:10px;
                    padding:16px 20px;font-size:14px;color:#aac4ff;line-height:1.7;">
                    {st.session_state.ai_summary}
                </div>
                """,
                unsafe_allow_html=True
            )

    with tab_team:
        show_team_tab(picks, players, pos_map, gw, bootstrap)
        
    with tab_transfers:
        show_transfers_tab(bootstrap, players, pos_map, live_bank_balance)
    
    with tab_history:
        show_history_tab(manager, st.session_state.team_id)

# ── Router ────────────────────────────────────────────────
if st.session_state.logged_in:
    show_dashboard()
else:
    show_login()