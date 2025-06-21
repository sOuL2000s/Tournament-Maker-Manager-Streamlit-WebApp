import streamlit as st
import firebase_admin
from firebase_admin import credentials, firestore
import uuid
import os
import base64
import tempfile

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
    def create_tournament(name, type_):
        tid = str(uuid.uuid4())
        db.collection("tournaments").document(tid).set({
            "name": name,
            "type": type_,
            "teams": [],
            "matches": [],
            "created": firestore.SERVER_TIMESTAMP
        })
        return tid

    def get_tournaments():
        return db.collection("tournaments").stream()

    def add_team(tid, team_name):
        doc = db.collection("tournaments").document(tid)
        doc.update({"teams": firestore.ArrayUnion([team_name])})

    def generate_round_robin_matches(teams):
        matches = []
        n = len(teams)
        for i in range(n):
            for j in range(i + 1, n):
                matches.append({
                    "team1": teams[i],
                    "team2": teams[j],
                    "score1": None,
                    "score2": None
                })
        return matches

    def save_matches(tid, matches):
        doc = db.collection("tournaments").document(tid)
        doc.update({"matches": matches})

    def update_match_score(tid, index, score1, score2):
        doc = db.collection("tournaments").document(tid).get()
        matches = doc.to_dict().get("matches", [])
        matches[index]["score1"] = score1
        matches[index]["score2"] = score2
        db.collection("tournaments").document(tid).update({"matches": matches})

    # --- Streamlit UI ---
    st.set_page_config(page_title="Tournament Maker", page_icon="🏆")
    st.title("🏆 Tournament Maker")

    menu = st.sidebar.selectbox("Menu", ["Create Tournament", "Manage Tournament"])

    if menu == "Create Tournament":
        t_name = st.text_input("Tournament Name")
        t_type = st.selectbox("Type", ["Round Robin", "Single Elimination"])  # Only Round Robin supported
        if st.button("Create"):
            tid = create_tournament(t_name, t_type)
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

            st.subheader(f"Tournament: {data['name']} ({data['type']})")

            with st.expander("➕ Add Team"):
                new_team = st.text_input("Team Name")
                if st.button("Add Team"):
                    add_team(tid, new_team)
                    st.rerun()

            st.write("### Teams")
            st.write(data.get("teams", []))

            if data["type"] == "Round Robin" and not data.get("matches"):
                if st.button("Generate Fixtures"):
                    matches = generate_round_robin_matches(data["teams"])
                    save_matches(tid, matches)
                    st.success("Matches Generated.")
                    st.rerun()

            if data.get("matches"):
                st.write("### Matches")
                for idx, match in enumerate(data["matches"]):
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.write(f"{match['team1']} vs {match['team2']}")
                    with col2:
                        score1 = st.number_input(f"{match['team1']} Score", key=f"s1_{idx}", value=match['score1'] or 0)
                        score2 = st.number_input(f"{match['team2']} Score", key=f"s2_{idx}", value=match['score2'] or 0)
                    with col3:
                        if st.button("Update", key=f"u_{idx}"):
                            update_match_score(tid, idx, score1, score2)
                            st.rerun()

                # Leaderboard
                st.write("### Leaderboard")
                leaderboard = {team: 0 for team in data["teams"]}
                for m in data["matches"]:
                    if m["score1"] is not None and m["score2"] is not None:
                        if m["score1"] > m["score2"]:
                            leaderboard[m["team1"]] += 3
                        elif m["score1"] < m["score2"]:
                            leaderboard[m["team2"]] += 3
                        else:
                            leaderboard[m["team1"]] += 1
                            leaderboard[m["team2"]] += 1
                sorted_leaderboard = sorted(leaderboard.items(), key=lambda x: x[1], reverse=True)
                st.table(sorted_leaderboard)
