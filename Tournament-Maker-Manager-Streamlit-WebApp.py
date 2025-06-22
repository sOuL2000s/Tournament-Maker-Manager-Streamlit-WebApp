import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import uuid
import os
import base64
import tempfile
import random
import pandas as pd

# --- Firebase Secure Setup (Render-Compatible) ---
firebase_key_b64 = os.getenv("FIREBASE_KEY_B64")

if firebase_key_b64 is None:
    st.error("❌ Firebase credentials not found. Please set FIREBASE_KEY_B64 in environment variables.")
else:
    firebase_json = base64.b64decode(firebase_key_b64.encode())

    with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as tmp:
        tmp.write(firebase_json)
        tmp_path = tmp.name

    cred = credentials.Certificate(tmp_path)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
    db = firestore.client()

    # --- Firestore Functions ---
    def create_tournament(name, type_, sport):
        tid = str(uuid.uuid4())
        db.collection("tournaments").document(tid).set({
            "name": name,
            "type": type_,
            "sport": sport,
            "teams": [],
            "matches": [],
            "created": firestore.SERVER_TIMESTAMP,
            "scorers": {},     # football/basketball
            "assists": {},     # football/basketball
            "runs": {},        # cricket
            "wickets": {},     # cricket
            "points": {},      # basketball
            "sets": {}         # badminton
        })
        return tid


    def get_tournaments():
        return db.collection("tournaments").stream()

    def delete_tournament(tid):
        db.collection("tournaments").document(tid).delete()

    def add_team(tid, team_name):
        doc = db.collection("tournaments").document(tid)
        doc.update({"teams": firestore.ArrayUnion([team_name])})

    def generate_round_robin_matches(teams):
        random.shuffle(teams)  # Shuffle teams for random pairing
        n = len(teams)
        if n % 2:
            teams.append("BYE")  # Add BYE if odd number

        rounds = n - 1
        matchdays = []

        for rnd in range(rounds):
            day_matches = []
            for i in range(n // 2):
                t1 = teams[i]
                t2 = teams[n - 1 - i]
                if t1 != "BYE" and t2 != "BYE":
                    day_matches.append({
                        "team1": t1,
                        "team2": t2,
                        "score1": None,
                        "score2": None
                    })
            matchdays.append(day_matches)
            # Rotate teams (except first)
            teams = [teams[0]] + [teams[-1]] + teams[1:-1]

        # Flatten rounds to a single list of matches
        return [match for day in matchdays for match in day]

    def generate_knockout_matches(teams):
        random.shuffle(teams)
        matches = []
        for i in range(0, len(teams), 2):
            team2 = teams[i + 1] if i + 1 < len(teams) else "BYE"
            matches.append({"team1": teams[i], "team2": team2, "score1": None, "score2": None})
        return matches

    def save_matches(tid, matches):
        db.collection("tournaments").document(tid).update({"matches": matches})

    def update_match_score(tid, index, score1, score2):
        doc = db.collection("tournaments").document(tid).get()
        matches = doc.to_dict().get("matches", [])
        matches[index]["score1"] = score1
        matches[index]["score2"] = score2
        db.collection("tournaments").document(tid).update({"matches": matches})

    def update_scorer(tid, player_name):
        ref = db.collection("tournaments").document(tid)
        ref.update({f"scorers.{player_name}": firestore.Increment(1)})

    def update_assist(tid, player_name):
        ref = db.collection("tournaments").document(tid)
        ref.update({f"assists.{player_name}": firestore.Increment(1)})

    def update_stat(tid, category, player_name):
        ref = db.collection("tournaments").document(tid)
        ref.update({f"{category}.{player_name}": firestore.Increment(1)})    

    # --- Streamlit UI ---
    st.set_page_config(page_title="Tournament Manager", page_icon="🏆")
    st.title("🏆 Tournament Manager")

    menu = st.sidebar.selectbox("Menu", ["Create Tournament", "Manage Tournament"])
    type_display = {
        "League (Round Robin)": "League",
        "Premier League (Round Robin)": "Premier League",
        "Knockout (Elimination)": "Knockout",
        "Combination (Group + Knockout)": "Group + Knockout"
    }

    sports_supported = ["Football", "Cricket", "Basketball", "Badminton"]

    if menu == "Create Tournament":
        t_name = st.text_input("Tournament Name")
        t_type = st.selectbox("Type", list(type_display.keys()))
        sport = st.selectbox("Sport", sports_supported)
        teams_input = st.text_area("Enter team names (one per line)")
        if st.button("Create"):
            tid = create_tournament(t_name, t_type, sport)
            for team in teams_input.splitlines():
                if team.strip():
                    add_team(tid, team.strip())
            st.success(f"Tournament Created! ID: {tid}")

    elif menu == "Manage Tournament":
        tournaments = list(get_tournaments())
        t_dict = {t.get("name"): t.id for t in tournaments}

        if not t_dict:
            st.warning("No tournaments found.")
        else:
            selected = st.selectbox("Select Tournament", list(t_dict.keys()))
            tid = t_dict[selected]
            doc = db.collection("tournaments").document(tid).get()
            data = doc.to_dict()

            tabs = st.tabs(["🏠 Overview", "📋 Matches", "🏅 Leaderboard", "📊 Stats"])

            with tabs[0]:
                st.header(f"🏠 Overview: {data['name']} ({type_display.get(data['type'], data['type'])})")
                st.write(f"📌 Sport: {data['sport']}")
                st.write("### Teams")
                st.dataframe(pd.DataFrame({"Team Name": data.get("teams", [])}))
                if st.button("❌ Delete Tournament"):
                    delete_tournament(tid)
                    st.success("Tournament deleted.")
                    st.rerun()

            with tabs[1]:
                st.header("📋 Match Fixtures")
                if not data.get("matches"):
                    if st.button("Generate Fixtures"):
                        if "Knockout" in data["type"]:
                            matches = generate_knockout_matches(data["teams"])
                        elif "Combination" in data["type"]:
                            teams = data["teams"]
                            random.shuffle(teams)
                            mid = len(teams) // 2
                            group_a = teams[:mid]
                            group_b = teams[mid:]
                            matches = generate_round_robin_matches(group_a) + generate_round_robin_matches(group_b)
                            matches.append({"team1": "Group A Winner", "team2": "Group B Winner", "score1": None, "score2": None})
                        else:
                            matches = generate_round_robin_matches(data["teams"])
                        save_matches(tid, matches)
                        st.success("Fixtures Generated.")
                        st.rerun()

                if data.get("matches"):
                    match_display = []
                    for idx, match in enumerate(data["matches"], start=1):
                        match_display.append({"#": idx, "Team 1": match["team1"], "Team 2": match["team2"], "Score": f"{match['score1']} - {match['score2']}"})
                    st.dataframe(pd.DataFrame(match_display), use_container_width=True)

                    for idx, match in enumerate(data["matches"]):
                        st.markdown(f"#### ⚔️ Match {idx + 1}: {match['team1']} vs {match['team2']}")
                        col1, col2 = st.columns(2)
                        score1 = col1.number_input(f"{match['team1']} Score", key=f"s1_{idx}", value=match['score1'] or 0)
                        score2 = col2.number_input(f"{match['team2']} Score", key=f"s2_{idx}", value=match['score2'] or 0)

                        if data["sport"] == "Football":
                            scorer_input = st.text_input("⚽ Goal Scorers (comma-separated)", key=f"goal_{idx}")
                            assist_input = st.text_input("🎯 Assisters (comma-separated)", key=f"assist_{idx}")

                        elif data["sport"] == "Cricket":
                            runs_input = st.text_input("🏏 Run Scorers (comma-separated)", key=f"runs_{idx}")
                            wickets_input = st.text_input("🎯 Wicket Takers (comma-separated)", key=f"wickets_{idx}")

                        elif data["sport"] == "Basketball":
                            points_input = st.text_input("🏀 Points Scorers (comma-separated)", key=f"points_{idx}")
                            assist_input = st.text_input("🎯 Assisters (comma-separated)", key=f"bball_assist_{idx}")

                        elif data["sport"] == "Badminton":
                            sets_input = st.text_input("🏸 Set Winners (comma-separated)", key=f"sets_{idx}")

                        if st.button("Update Match", key=f"update_{idx}"):
                            update_match_score(tid, idx, score1, score2)

                            # Football Stats
                            if data["sport"] == "Football":
                                for name in [p.strip() for p in scorer_input.split(",") if p.strip()]:
                                    update_stat(tid, "scorers", name)
                                for name in [p.strip() for p in assist_input.split(",") if p.strip()]:
                                    update_stat(tid, "assists", name)

                            # Cricket Stats
                            elif data["sport"] == "Cricket":
                                for name in [p.strip() for p in runs_input.split(",") if p.strip()]:
                                    update_stat(tid, "runs", name)
                                for name in [p.strip() for p in wickets_input.split(",") if p.strip()]:
                                    update_stat(tid, "wickets", name)

                            # Basketball Stats
                            elif data["sport"] == "Basketball":
                                for name in [p.strip() for p in points_input.split(",") if p.strip()]:
                                    update_stat(tid, "points", name)
                                for name in [p.strip() for p in assist_input.split(",") if p.strip()]:
                                    update_stat(tid, "assists", name)

                            # Badminton Stats
                            elif data["sport"] == "Badminton":
                                for name in [p.strip() for p in sets_input.split(",") if p.strip()]:
                                    update_stat(tid, "sets", name)

                            st.success("✅ Match score and stats updated!")
                            st.rerun()

            with tabs[2]:
                st.header("🏅 Leaderboard")
                sport = data.get("sport", "Football")
                leaderboard = {team: {"P": 0, "W": 0, "D": 0, "L": 0, "Pts": 0, "F": 0, "A": 0} for team in data["teams"]}

                for m in data.get("matches", []):
                    t1, t2 = m["team1"], m["team2"]
                    s1, s2 = m["score1"], m["score2"]
                    if s1 is not None and s2 is not None:
                        leaderboard[t1]["P"] += 1
                        leaderboard[t2]["P"] += 1
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
                        else:
                            leaderboard[t1]["D"] += 1
                            leaderboard[t2]["D"] += 1
                            leaderboard[t1]["Pts"] += 1
                            leaderboard[t2]["Pts"] += 1

                lb_df = pd.DataFrame.from_dict(leaderboard, orient="index").reset_index()
                lb_df = lb_df.rename(columns={"index": "Team"})
                lb_df["GD"] = lb_df["F"] - lb_df["A"]
                lb_df = lb_df.sort_values(by=["Pts", "GD", "F"], ascending=False)
                st.dataframe(lb_df, use_container_width=True)

            with tabs[3]:
                st.header("📊 Stats")
                sport = data.get("sport", "Football")

                if sport == "Football":
                    scorer = st.text_input("Goal Scorer")
                    if st.button("Add Goal"):
                        update_stat(tid, "scorers", scorer)
                        st.success(f"Added goal for {scorer}")
                        st.rerun()

                    assister = st.text_input("Assist Provider")
                    if st.button("Add Assist"):
                        update_stat(tid, "assists", assister)
                        st.success(f"Added assist for {assister}")
                        st.rerun()

                    st.write("### Top Scorers")
                    scorers = sorted(data.get("scorers", {}).items(), key=lambda x: x[1], reverse=True)
                    if scorers:
                        st.dataframe(pd.DataFrame(scorers, columns=["Player", "Goals"]))

                    st.write("### Top Assists")
                    assists = sorted(data.get("assists", {}).items(), key=lambda x: x[1], reverse=True)
                    if assists:
                        st.dataframe(pd.DataFrame(assists, columns=["Player", "Assists"]))

                elif sport == "Cricket":
                    batsman = st.text_input("Batsman who scored runs")
                    if st.button("Add Run"):
                        update_stat(tid, "runs", batsman)
                        st.success(f"Added run for {batsman}")
                        st.rerun()

                    bowler = st.text_input("Bowler who took wicket")
                    if st.button("Add Wicket"):
                        update_stat(tid, "wickets", bowler)
                        st.success(f"Added wicket for {bowler}")
                        st.rerun()

                    st.write("### Top Scorers")
                    runs = sorted(data.get("runs", {}).items(), key=lambda x: x[1], reverse=True)
                    if runs:
                        st.dataframe(pd.DataFrame(runs, columns=["Batsman", "Runs"]))

                    st.write("### Top Wicket Takers")
                    wickets = sorted(data.get("wickets", {}).items(), key=lambda x: x[1], reverse=True)
                    if wickets:
                        st.dataframe(pd.DataFrame(wickets, columns=["Bowler", "Wickets"]))

                elif sport == "Basketball":
                    scorer = st.text_input("Player who scored points")
                    if st.button("Add Point"):
                        update_stat(tid, "points", scorer)
                        st.success(f"Added point for {scorer}")
                        st.rerun()

                    assister = st.text_input("Player who assisted")
                    if st.button("Add Assist"):
                        update_stat(tid, "assists", assister)
                        st.success(f"Added assist for {assister}")
                        st.rerun()

                    points = sorted(data.get("points", {}).items(), key=lambda x: x[1], reverse=True)
                    st.write("### Top Scorers")
                    if points:
                        st.dataframe(pd.DataFrame(points, columns=["Player", "Points"]))

                    assists = sorted(data.get("assists", {}).items(), key=lambda x: x[1], reverse=True)
                    st.write("### Top Assists")
                    if assists:
                        st.dataframe(pd.DataFrame(assists, columns=["Player", "Assists"]))

                elif sport == "Badminton":
                    player = st.text_input("Player who won a set")
                    if st.button("Add Set Win"):
                        update_stat(tid, "sets", player)
                        st.success(f"Added set win for {player}")
                        st.rerun()

                    sets = sorted(data.get("sets", {}).items(), key=lambda x: x[1], reverse=True)
                    st.write("### Top Set Winners")
                    if sets:
                        st.dataframe(pd.DataFrame(sets, columns=["Player", "Sets Won"]))
