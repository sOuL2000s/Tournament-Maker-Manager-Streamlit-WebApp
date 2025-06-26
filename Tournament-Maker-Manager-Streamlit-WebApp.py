import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import uuid
import os
import base64
import tempfile
import random
import pandas as pd
from datetime import datetime

# --- Constants & Page Configuration ---
APP_TITLE = "🏆 Tournament Manager"
PAGE_ICON = "🏆"
TOURNAMENT_TYPES = {
    "League (Round Robin)": "League",
    "Premier League (Round Robin)": "Premier League",
    "Knockout (Elimination)": "Knockout",
    "Combination (Group + Knockout)": "Group + Knockout"
}
SPORTS_SUPPORTED = ["Football", "Cricket", "Basketball", "Badminton"]

st.set_page_config(page_title=APP_TITLE, page_icon=PAGE_ICON, layout="wide")
st.title(APP_TITLE)

# --- Firebase Secure Setup (Render-Compatible) ---
@st.cache_resource
def initialize_firebase():
    """Initializes Firebase Admin SDK securely using environment variable."""
    firebase_key_b64 = os.getenv("FIREBASE_KEY_B64")

    if not firebase_key_b64:
        st.error("❌ Firebase credentials not found. Please set FIREBASE_KEY_B64 in environment variables.")
        st.stop()

    try:
        firebase_json = base64.b64decode(firebase_key_b64.encode())
        # Use NamedTemporaryFile to ensure the file is handled correctly across OS
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
            tmp.write(firebase_json)
            tmp_path = tmp.name

        cred = credentials.Certificate(tmp_path)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        # Clean up the temporary file immediately after initialization
        os.unlink(tmp_path)
        return firestore.client()
    except Exception as e:
        st.error(f"❌ Failed to initialize Firebase: {e}")
        st.stop()

db = initialize_firebase()

# --- Utility Functions ---
@st.cache_data(ttl=60) # Cache data for 60 seconds
def get_all_tournaments_data():
    """Fetches all tournaments from Firestore."""
    try:
        docs = db.collection("tournaments").stream()
        return {doc.id: doc.to_dict() for doc in docs}
    except Exception as e:
        st.error(f"Error fetching tournaments: {e}")
        return {}

def update_firestore_doc(collection, doc_id, data_to_update):
    """Generic function to update a Firestore document."""
    try:
        db.collection(collection).document(doc_id).update(data_to_update)
        st.session_state["refresh_data"] = True # Trigger a data refresh
        return True
    except Exception as e:
        st.error(f"Error updating {collection} document {doc_id}: {e}")
        return False

# --- Firebase CRUD Operations ---
def create_tournament(name, type_, sport):
    """Creates a new tournament in Firestore."""
    tid = str(uuid.uuid4())
    initial_data = {
        "name": name,
        "type": type_,
        "sport": sport,
        "teams": [],
        "players": {}, # New: Store players per team {team_name: [player1, player2]}
        "matches": [],
        "created_at": firestore.SERVER_TIMESTAMP,
        "scorers": {},
        "assists": {},
        "runs": {},
        "wickets": {},
        "points": {},
        "sets": {}
    }
    try:
        db.collection("tournaments").document(tid).set(initial_data)
        st.session_state["refresh_data"] = True
        return tid
    except Exception as e:
        st.error(f"Error creating tournament: {e}")
        return None

def delete_tournament(tid):
    """Deletes a tournament from Firestore."""
    try:
        db.collection("tournaments").document(tid).delete()
        st.session_state["refresh_data"] = True
        return True
    except Exception as e:
        st.error(f"Error deleting tournament: {e}")
        return False

def add_team_to_tournament(tid, team_name):
    """Adds a team to an existing tournament."""
    if update_firestore_doc("tournaments", tid, {"teams": firestore.ArrayUnion([team_name])}):
        update_firestore_doc("tournaments", tid, {f"players.{team_name}": []}) # Initialize empty player list for new team
        return True
    return False

def remove_team_from_tournament(tid, team_name):
    """Removes a team from an existing tournament."""
    if update_firestore_doc("tournaments", tid, {"teams": firestore.ArrayRemove([team_name])}):
        # Also remove players associated with this team
        doc_ref = db.collection("tournaments").document(tid)
        doc_data = doc_ref.get().to_dict()
        if doc_data and "players" in doc_data:
            players = doc_data["players"]
            if team_name in players:
                del players[team_name]
                update_firestore_doc("tournaments", tid, {"players": players})
        return True
    return False

def add_player_to_team(tid, team_name, player_name):
    """Adds a player to a specific team within a tournament."""
    doc_ref = db.collection("tournaments").document(tid)
    doc_data = doc_ref.get().to_dict()
    if doc_data and "players" in doc_data:
        players = doc_data["players"]
        if team_name in players:
            if player_name not in players[team_name]: # Avoid duplicates
                players[team_name].append(player_name)
                return update_firestore_doc("tournaments", tid, {"players": players})
    return False

def remove_player_from_team(tid, team_name, player_name):
    """Removes a player from a specific team within a tournament."""
    doc_ref = db.collection("tournaments").document(tid)
    doc_data = doc_ref.get().to_dict()
    if doc_data and "players" in doc_data:
        players = doc_data["players"]
        if team_name in players and player_name in players[team_name]:
            players[team_name].remove(player_name)
            return update_firestore_doc("tournaments", tid, {"players": players})
    return False

def save_matches(tid, matches):
    """Saves generated matches to a tournament."""
    return update_firestore_doc("tournaments", tid, {"matches": matches})

def update_match_score(tid, index, score1, score2):
    """Updates the score of a specific match."""
    doc = db.collection("tournaments").document(tid).get()
    matches = doc.to_dict().get("matches", [])
    if 0 <= index < len(matches):
        matches[index]["score1"] = score1
        matches[index]["score2"] = score2
        return update_firestore_doc("tournaments", tid, {"matches": matches})
    return False

def update_player_stat(tid, category, player_name, increment_by=1):
    """Increments a player's stat by a given amount."""
    ref = db.collection("tournaments").document(tid)
    return update_firestore_doc("tournaments", tid, {f"{category}.{player_name}": firestore.Increment(increment_by)})

# --- Match Generation Logic ---
def generate_round_robin_matches(teams):
    """Generates round-robin fixtures."""
    if not teams or len(teams) < 2:
        return []

    # Ensure we work with a copy and remove BYE for actual display later
    active_teams = teams[:]
    n = len(active_teams)
    if n % 2 != 0:
        active_teams.append("BYE")
        n += 1 # Update n for BYE team

    rounds = n - 1
    all_matches = []

    for rnd in range(rounds):
        for i in range(n // 2):
            team1 = active_teams[i]
            team2 = active_teams[n - 1 - i]
            if team1 != "BYE" and team2 != "BYE":
                all_matches.append({
                    "team1": team1,
                    "team2": team2,
                    "score1": None,
                    "score2": None,
                    "round": rnd + 1 # Track round for display
                })
        # Rotate teams (except the first one if n is even, or the 'fixed' team if n is odd)
        if n > 2: # Only rotate if there are enough teams
            # The 'fixed' team is always teams[0].
            # Remaining teams are rotated.
            rotated_part = [active_teams[n-1]] + active_teams[1:n-1]
            active_teams = [active_teams[0]] + rotated_part

    return all_matches

def generate_knockout_matches(teams):
    """Generates knockout fixtures (simple initial pairings)."""
    if not teams or len(teams) < 2:
        return []
    random.shuffle(teams)
    matches = []
    # Ensure even number of teams for initial pairing, add BYE if needed
    num_teams = len(teams)
    if num_teams % 2 != 0:
        teams.append("BYE")

    for i in range(0, len(teams), 2):
        team1 = teams[i]
        team2 = teams[i + 1] if i + 1 < len(teams) else "BYE"
        if team1 != "BYE" and team2 != "BYE":
            matches.append({"team1": team1, "team2": team2, "score1": None, "score2": None, "round": 1})
    return matches

def generate_combination_matches(teams):
    """Generates group stage (round-robin) and a final knockout match."""
    if len(teams) < 4: # Need at least 4 teams for a reasonable combination
        st.warning("Combination tournament requires at least 4 teams for meaningful groups.")
        return []

    random.shuffle(teams)
    mid = len(teams) // 2
    group_a_teams = teams[:mid]
    group_b_teams = teams[mid:]

    group_a_matches = generate_round_robin_matches(group_a_teams)
    group_b_matches = generate_round_robin_matches(group_b_teams)

    # Label matches with their group
    for m in group_a_matches: m["group"] = "Group A"
    for m in group_b_matches: m["group"] = "Group B"

    # Placeholder for knockout stage. Actual knockout participants determined after group stage completion.
    knockout_match = {
        "team1": "Group A Winner",
        "team2": "Group B Winner",
        "score1": None,
        "score2": None,
        "round": "Final" # Special round for the final
    }
    return group_a_matches + group_b_matches + [knockout_match]

# --- Leaderboard Calculation ---
def calculate_leaderboard(matches, teams, sport_type):
    """Calculates and returns the leaderboard for a given tournament."""
    leaderboard = {team: {"P": 0, "W": 0, "D": 0, "L": 0, "Pts": 0, "F": 0, "A": 0, "GD": 0} for team in teams}

    for m in matches:
        t1, t2 = m["team1"], m["team2"]
        s1, s2 = m["score1"], m["score2"]

        # Only process completed matches (both scores entered)
        if s1 is not None and s2 is not None:
            # Ensure teams exist in the initial leaderboard structure
            # This handles cases where matches might contain 'BYE' or 'Group X Winner' placeholders
            if t1 not in leaderboard or t2 not in leaderboard:
                continue

            leaderboard[t1]["P"] += 1
            leaderboard[t2]["P"] += 1

            if sport_type in ["Football", "Basketball", "Cricket"]: # Score-based sports
                leaderboard[t1]["F"] += s1
                leaderboard[t1]["A"] += s2
                leaderboard[t2]["F"] += s2
                leaderboard[t2]["A"] += s1

                if s1 > s2:
                    leaderboard[t1]["W"] += 1
                    leaderboard[t2]["L"] += 1
                    leaderboard[t1]["Pts"] += 3
                elif s1 < s2:
                    leaderboard[t2]["W"] += 1
                    leaderboard[t1]["L"] += 1
                    leaderboard[t2]["Pts"] += 3
                else: # Draw
                    leaderboard[t1]["D"] += 1
                    leaderboard[t2]["D"] += 1
                    leaderboard[t1]["Pts"] += 1
                    leaderboard[t2]["Pts"] += 1
            elif sport_type == "Badminton": # Set-based sport (simple win/loss based on sets)
                # Assuming s1 and s2 represent sets won by each team/player
                if s1 > s2:
                    leaderboard[t1]["W"] += 1
                    leaderboard[t2]["L"] += 1
                    leaderboard[t1]["Pts"] += 1 # 1 point for a set win
                elif s1 < s2:
                    leaderboard[t2]["W"] += 1
                    leaderboard[t1]["L"] += 1
                    leaderboard[t2]["Pts"] += 1
                else:
                    leaderboard[t1]["D"] += 1
                    leaderboard[t2]["D"] += 1
                    # No points for a draw in badminton typically, adjust as needed

    lb_df = pd.DataFrame.from_dict(leaderboard, orient="index").reset_index()
    lb_df = lb_df.rename(columns={"index": "Team"})
    if sport_type in ["Football", "Basketball", "Cricket"]:
        lb_df["GD"] = lb_df["F"] - lb_df["A"]
        lb_df = lb_df.sort_values(by=["Pts", "GD", "F"], ascending=[False, False, False])
    else: # Badminton or other sports where GD/F/A might not apply
        lb_df = lb_df.sort_values(by=["Pts", "W"], ascending=[False, False])
    return lb_df.set_index("Team")


# --- Streamlit UI Components ---

# Session state initialization for data refresh
if "refresh_data" not in st.session_state:
    st.session_state["refresh_data"] = False

# Force data refresh if needed
if st.session_state["refresh_data"]:
    get_all_tournaments_data.clear() # Clear cache for this function
    st.session_state["refresh_data"] = False # Reset flag

tournaments_data = get_all_tournaments_data()

with st.sidebar:
    st.header("Navigation")
    menu = st.radio("Choose Section", ["Create New Tournament", "Manage Existing Tournaments"])
    st.markdown("---")
    st.markdown("Developed with ❤️ by **Souparna Paul**")


# --- Create Tournament Section ---
if menu == "Create New Tournament":
    st.header("➕ Create a New Tournament")
    with st.form("create_tournament_form"):
        t_name = st.text_input("Tournament Name", placeholder="e.g., Summer Football Cup")
        t_type_key = st.selectbox("Tournament Type", list(TOURNAMENT_TYPES.keys()))
        sport = st.selectbox("Sport", SPORTS_SUPPORTED)
        teams_input = st.text_area("Enter initial team names (one per line, min 2)", height=150,
                                   placeholder="Team A\nTeam B\nTeam C")

        submitted = st.form_submit_button("Create Tournament")
        if submitted:
            team_list = [team.strip() for team in teams_input.splitlines() if team.strip()]
            if not t_name:
                st.error("❗ Tournament Name cannot be empty.")
            elif len(team_list) < 2:
                st.error("❗ Please enter at least 2 teams.")
            else:
                with st.spinner("Creating tournament..."):
                    tid = create_tournament(t_name, TOURNAMENT_TYPES[t_type_key], sport)
                    if tid:
                        for team in team_list:
                            add_team_to_tournament(tid, team) # Adds teams and initializes players dict
                        st.success(f"🎉 Tournament '{t_name}' created successfully! ID: `{tid}`")
                        st.session_state["selected_tournament_id"] = tid # Auto-select
                        st.session_state["refresh_data"] = True # Ensure UI updates
                        st.rerun() # Rerun to switch to manage section


# --- Manage Tournament Section ---
elif menu == "Manage Existing Tournaments":
    if not tournaments_data:
        st.warning("No tournaments found. Create one first!")
        st.stop()

    tournament_names = {data["name"]: tid for tid, data in tournaments_data.items()}
    
    # Pre-select if a tournament was just created
    default_index = 0
    if "selected_tournament_id" in st.session_state and st.session_state["selected_tournament_id"] in tournament_names.values():
        default_index = list(tournament_names.values()).index(st.session_state["selected_tournament_id"])

    selected_tournament_name = st.selectbox(
        "Select Tournament to Manage",
        list(tournament_names.keys()),
        index=default_index,
        key="tournament_selector"
    )
    
    tid = tournament_names[selected_tournament_name]
    current_tournament = tournaments_data[tid]

    st.markdown("---")
    st.subheader(f"⚙️ Managing: **{current_tournament['name']}**")
    st.markdown(f"**Type:** {TOURNAMENT_TYPES.get(current_tournament['type'], current_tournament['type'])} | **Sport:** {current_tournament['sport']}")

    tabs = st.tabs(["🏠 Overview", "👥 Teams & Players", "📋 Matches", "🏅 Leaderboard", "📊 Stats", "🗑️ Danger Zone"])

    # --- Overview Tab ---
    with tabs[0]:
        st.header(f"🏠 Overview: {current_tournament['name']}")
        # Safely display creation date, handling older tournaments without the field
        created_at_timestamp = current_tournament.get("created_at")
        if created_at_timestamp:
            # Firestore Timestamps have a .timestamp() method
            st.write(f"**Created:** {datetime.fromtimestamp(created_at_timestamp.timestamp()).strftime('%Y-%m-%d %H:%M')}")
        else:
            st.write("**Created:** *Date not available*")
        st.info(f"Tournament ID: `{tid}`")

        st.subheader("Current Teams")
        if current_tournament.get("teams"):
            st.dataframe(pd.DataFrame({"Team Name": current_tournament["teams"]}), use_container_width=True)
        else:
            st.info("No teams added yet.")

    # --- Teams & Players Tab ---
    with tabs[1]:
        st.header("👥 Team & Player Management")

        st.subheader("Add/Remove Teams")
        col_add_team, col_remove_team = st.columns(2)
        with col_add_team:
            new_team_name = st.text_input("New Team Name")
            if st.button("Add Team", key="add_team_btn"):
                if new_team_name.strip():
                    if new_team_name.strip() not in current_tournament.get("teams", []):
                        if add_team_to_tournament(tid, new_team_name.strip()):
                            st.success(f"Team '{new_team_name}' added.")
                            st.rerun()
                        else:
                            st.error("Failed to add team.")
                    else:
                        st.warning("Team already exists.")
                else:
                    st.warning("Team name cannot be empty.")
        with col_remove_team:
            if current_tournament.get("teams"):
                team_to_remove = st.selectbox("Select Team to Remove", current_tournament["teams"])
                if st.button("Remove Team", key="remove_team_btn", help="Removing a team is irreversible and will delete its players from the tournament data."):
                    if st.warning(f"Are you sure you want to remove '{team_to_remove}'? This cannot be undone."):
                        if remove_team_from_tournament(tid, team_to_remove):
                            st.success(f"Team '{team_to_remove}' removed.")
                            st.rerun()
                        else:
                            st.error("Failed to remove team.")
            else:
                st.info("No teams to remove.")

        st.markdown("---")
        st.subheader("Manage Players within Teams")
        if not current_tournament.get("teams"):
            st.info("Please add teams first to manage players.")
        else:
            selected_team_for_player = st.selectbox("Select Team", current_tournament["teams"], key="player_team_selector")

            players_in_selected_team = current_tournament.get("players", {}).get(selected_team_for_player, [])
            st.write(f"Players in {selected_team_for_player}: {', '.join(players_in_selected_team) if players_in_selected_team else 'No players yet.'}")

            col_add_player, col_remove_player = st.columns(2)
            with col_add_player:
                new_player_name = st.text_input(f"Add Player to {selected_team_for_player}")
                if st.button("Add Player", key="add_player_btn"):
                    if new_player_name.strip():
                        if add_player_to_team(tid, selected_team_for_player, new_player_name.strip()):
                            st.success(f"Player '{new_player_name}' added to {selected_team_for_player}.")
                            st.rerun()
                        else:
                            st.error("Failed to add player or player already exists.")
                    else:
                        st.warning("Player name cannot be empty.")
            with col_remove_player:
                if players_in_selected_team:
                    player_to_remove = st.selectbox(f"Remove Player from {selected_team_for_player}", players_in_selected_team)
                    if st.button("Remove Player", key="remove_player_btn"):
                        if remove_player_from_team(tid, selected_team_for_player, player_to_remove):
                            st.success(f"Player '{player_to_remove}' removed from {selected_team_for_player}.")
                            st.rerun()
                        else:
                            st.error("Failed to remove player.")
                else:
                    st.info("No players to remove from this team.")

    # --- Matches Tab ---
    with tabs[2]:
        st.header("📋 Match Fixtures")
        if not current_tournament.get("matches"):
            if not current_tournament.get("teams") or len(current_tournament["teams"]) < 2:
                st.warning("Please add at least two teams before generating fixtures.")
            else:
                if st.button("Generate Fixtures", help="Generate fixtures for the selected tournament type."):
                    with st.spinner("Generating matches..."):
                        generated_matches = []
                        if "Knockout" in current_tournament["type"]:
                            generated_matches = generate_knockout_matches(current_tournament["teams"])
                        elif "Combination" in current_tournament["type"]:
                            generated_matches = generate_combination_matches(current_tournament["teams"])
                        else: # League or Premier League
                            generated_matches = generate_round_robin_matches(current_tournament["teams"])

                        if generated_matches:
                            if save_matches(tid, generated_matches):
                                st.success("✅ Fixtures generated successfully!")
                                st.rerun()
                            else:
                                st.error("Failed to save matches.")
                        else:
                            st.warning("Could not generate matches. Ensure enough teams are added for the selected tournament type.")
        else:
            st.info(f"Total Matches: **{len(current_tournament['matches'])}**")

            match_data_for_display = []
            for idx, match in enumerate(current_tournament["matches"]):
                match_display_info = {
                    "Match #": idx + 1,
                    "Team 1": match["team1"],
                    "Team 2": match["team2"],
                    "Score": f"{match['score1'] or '?'} - {match['score2'] or '?'}"
                }
                if "group" in match:
                    match_display_info["Group"] = match["group"]
                if "round" in match:
                    match_display_info["Round"] = match["round"]
                match_data_for_display.append(match_display_info)

            st.dataframe(pd.DataFrame(match_data_for_display), use_container_width=True)

            st.markdown("---")
            st.subheader("Update Match Scores & Stats")

            for idx, match in enumerate(current_tournament["matches"]):
                st.markdown(f"#### ⚔️ Match {idx + 1}: {match['team1']} vs {match['team2']}")
                if "group" in match:
                    st.caption(f"Group: {match['group']}")
                if "round" in match:
                    st.caption(f"Round: {match['round']}")

                col_score1, col_score2 = st.columns(2)
                score1 = col_score1.number_input(f"Score for {match['team1']}", key=f"s1_{idx}", value=match['score1'] or 0, min_value=0)
                score2 = col_score2.number_input(f"Score for {match['team2']}", key=f"s2_{idx}", value=match['score2'] or 0, min_value=0)

                # Collect all potential players for stat input for this match (from both teams)
                all_players = []
                team1_players = current_tournament.get("players", {}).get(match["team1"], [])
                team2_players = current_tournament.get("players", {}).get(match["team2"], [])
                all_players.extend(team1_players)
                all_players.extend(team2_players)
                all_players = sorted(list(set(all_players))) # Unique and sorted

                if current_tournament["sport"] == "Football":
                    st.markdown("##### Football Stats")
                    scorer_player = st.selectbox("⚽ Goal Scorer", [""] + all_players, key=f"goal_player_{idx}")
                    assist_player = st.selectbox("🎯 Assist Provider", [""] + all_players, key=f"assist_player_{idx}")
                    
                    col_football_stat_buttons = st.columns(2)
                    if col_football_stat_buttons[0].button(f"Add Goal for {scorer_player}", key=f"add_goal_{idx}", disabled=not scorer_player):
                        if update_player_stat(tid, "scorers", scorer_player):
                            st.success(f"Goal recorded for {scorer_player}.")
                            st.rerun()
                    if col_football_stat_buttons[1].button(f"Add Assist for {assist_player}", key=f"add_assist_{idx}", disabled=not assist_player):
                        if update_player_stat(tid, "assists", assist_player):
                            st.success(f"Assist recorded for {assist_player}.")
                            st.rerun()

                elif current_tournament["sport"] == "Cricket":
                    st.markdown("##### Cricket Stats")
                    runs_scorer = st.selectbox("🏏 Batsman (Runs)", [""] + all_players, key=f"runs_scorer_{idx}")
                    wickets_taker = st.selectbox("🎯 Bowler (Wickets)", [""] + all_players, key=f"wickets_taker_{idx}")
                    
                    col_cricket_stat_buttons = st.columns(2)
                    if col_cricket_stat_buttons[0].button(f"Add Run for {runs_scorer}", key=f"add_run_{idx}", disabled=not runs_scorer):
                        if update_player_stat(tid, "runs", runs_scorer):
                            st.success(f"Run recorded for {runs_scorer}.")
                            st.rerun()
                    if col_cricket_stat_buttons[1].button(f"Add Wicket for {wickets_taker}", key=f"add_wicket_{idx}", disabled=not wickets_taker):
                        if update_player_stat(tid, "wickets", wickets_taker):
                            st.success(f"Wicket recorded for {wickets_taker}.")
                            st.rerun()

                elif current_tournament["sport"] == "Basketball":
                    st.markdown("##### Basketball Stats")
                    points_scorer = st.selectbox("🏀 Player (Points)", [""] + all_players, key=f"points_scorer_{idx}")
                    bball_assist_player = st.selectbox("🎯 Player (Assists)", [""] + all_players, key=f"bball_assist_player_{idx}")

                    col_bball_stat_buttons = st.columns(2)
                    if col_bball_stat_buttons[0].button(f"Add Point for {points_scorer}", key=f"add_point_{idx}", disabled=not points_scorer):
                        if update_player_stat(tid, "points", points_scorer):
                            st.success(f"Point recorded for {points_scorer}.")
                            st.rerun()
                    if col_bball_stat_buttons[1].button(f"Add Assist for {bball_assist_player}", key=f"add_bball_assist_{idx}", disabled=not bball_assist_player):
                        if update_player_stat(tid, "assists", bball_assist_player):
                            st.success(f"Assist recorded for {bball_assist_player}.")
                            st.rerun()

                elif current_tournament["sport"] == "Badminton":
                    st.markdown("##### Badminton Stats")
                    set_winner = st.selectbox("🏸 Player (Set Win)", [""] + all_players, key=f"set_winner_{idx}")
                    if st.button(f"Record Set Win for {set_winner}", key=f"add_set_win_{idx}", disabled=not set_winner):
                        if update_player_stat(tid, "sets", set_winner):
                            st.success(f"Set win recorded for {set_winner}.")
                            st.rerun()
                
                st.markdown("---") # Separator for score update button and next match
                if st.button("Save Match Score", key=f"update_match_score_{idx}"):
                    if update_match_score(tid, idx, score1, score2):
                        st.success("✅ Match score updated!")
                        st.rerun()
                    else:
                        st.error("Failed to update match score.")
                st.markdown("---") # Separator between matches


    # --- Leaderboard Tab ---
    with tabs[3]:
        st.header("🏅 Leaderboard")
        if current_tournament.get("matches") and current_tournament.get("teams"):
            leaderboard_df = calculate_leaderboard(
                current_tournament["matches"],
                current_tournament["teams"],
                current_tournament["sport"]
            )
            st.dataframe(leaderboard_df, use_container_width=True)

            # Download button
            csv_data = leaderboard_df.to_csv().encode('utf-8')
            st.download_button(
                label="Download Leaderboard as CSV",
                data=csv_data,
                file_name=f"{selected_tournament_name}_leaderboard.csv",
                mime="text/csv",
                key="download_leaderboard"
            )
        else:
            st.info("No matches played or teams added yet to generate a leaderboard.")

    # --- Stats Tab ---
    with tabs[4]:
        st.header("📊 Player Statistics")
        sport = current_tournament["sport"]

        # Define stat categories for each sport
        stat_categories = {
            "Football": {"scorers": "Goals", "assists": "Assists"},
            "Cricket": {"runs": "Runs", "wickets": "Wickets"},
            "Basketball": {"points": "Points", "assists": "Assists"},
            "Badminton": {"sets": "Sets Won"}
        }

        if sport in stat_categories:
            for category_key, category_name in stat_categories[sport].items():
                st.subheader(f"### Top {category_name}")
                stats_data = current_tournament.get(category_key, {})
                if stats_data:
                    sorted_stats = sorted(stats_data.items(), key=lambda x: x[1], reverse=True)
                    stats_df = pd.DataFrame(sorted_stats, columns=["Player", category_name])
                    st.dataframe(stats_df, use_container_width=True)
                    
                    # Download button for individual stats
                    csv_data = stats_df.to_csv().encode('utf-8')
                    st.download_button(
                        label=f"Download {category_name} Stats",
                        data=csv_data,
                        file_name=f"{selected_tournament_name}_{category_key}_stats.csv",
                        mime="text/csv",
                        key=f"download_{category_key}_stats"
                    )
                else:
                    st.info(f"No {category_name.lower()} recorded yet.")
        else:
            st.info("Statistics not configured for this sport yet.")

    # --- Danger Zone Tab ---
    with tabs[5]:
        st.header("🗑️ Danger Zone")
        st.warning("⚠️ **Warning: These actions are irreversible!**")

        st.markdown("### Reset All Matches and Stats")
        st.write("This will clear all match scores and player statistics for this tournament.")

        confirm_reset = st.checkbox("Confirm reset all matches and stats?", key="confirm_reset_matches_stats")

        if confirm_reset:
            if st.button("🚨 Reset Matches & Stats", help="This cannot be undone!", key="reset_matches_stats_btn"):
                with st.spinner("Resetting..."):
                    if update_firestore_doc("tournaments", tid, {
                        "matches": [],
                        "scorers": {},
                        "assists": {},
                        "runs": {},
                        "wickets": {},
                        "points": {},
                        "sets": {}
                    }):
                        st.success("Matches and stats reset successfully.")
                        st.session_state["refresh_data"] = True # Trigger refresh
                        st.rerun()
                    else:
                        st.error("Failed to reset matches and stats.")
        else:
            st.button("🚨 Reset Matches & Stats", disabled=True, help="Check the box to enable reset.", key="disabled_reset_matches_stats_btn")
            st.info("Please confirm the action by checking the box above.")


        st.markdown("### Delete Tournament")
        st.write("Permanently delete this tournament and all its associated data.")

        # The checkbox controls whether the delete button appears/is actionable
        confirm_delete = st.checkbox(f"Confirm deletion of '{selected_tournament_name}'?", key="confirm_delete_tournament")

        # Now, the delete button is only active if the checkbox is checked
        if confirm_delete:
            if st.button("🔥🔥🔥 Delete Tournament PERMANENTLY", help="This cannot be undone!", key="delete_tournament_btn"):
                with st.spinner("Deleting tournament..."):
                    if delete_tournament(tid):
                        st.success(f"Tournament '{selected_tournament_name}' deleted.")
                        # Clear selection and data cache, then rerun
                        if "selected_tournament_id" in st.session_state:
                            del st.session_state["selected_tournament_id"]
                        st.session_state["refresh_data"] = True
                        st.rerun()
                    else:
                        st.error("Failed to delete tournament.")
        else:
            # Display a disabled button or a message when not confirmed
            st.button("🔥🔥🔥 Delete Tournament PERMANENTLY", disabled=True, help="Check the box to enable deletion.", key="disabled_delete_tournament_btn")
            st.info("Please confirm deletion by checking the box above.")
