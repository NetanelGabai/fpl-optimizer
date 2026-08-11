import streamlit as st
import requests
import pandas as pd
import pulp

# --- הגדרות תצוגה ---
st.set_page_config(page_title="ליגה לרציניים בלבד - Analytics Hub", layout="wide", page_icon="⚽")

st.markdown("""
    <style>
    h1 { color: #FFD700; text-shadow: 2px 2px #000000; text-align: center; padding-bottom: 20px;}
    .stDataFrame { border: 2px solid #000000; border-radius: 5px; }
    </style>
""", unsafe_allow_html=True)

st.title("🏆 ליגה לרציניים בלבד - FPL Analytics & Solver Hub")

# --- טעינת נתונים ---
@st.cache_data(ttl=3600)
def load_fpl_data():
    url_static = 'https://fantasy.premierleague.com/api/bootstrap-static/'
    data_static = requests.get(url_static).json()

    teams_map = {team['id']: team['name'] for team in data_static['teams']}
    positions_map = {pos['id']: pos['singular_name'] for pos in data_static['element_types']}
    
    events_df = pd.DataFrame(data_static['events'])
    next_gw_rows = events_df[events_df['is_next'] == True]
    current_gw = int(next_gw_rows['id'].values[0]) if not next_gw_rows.empty else 1

    players_df = pd.DataFrame(data_static['elements'])
    players_df['team_name'] = players_df['team'].map(teams_map)
    players_df['position'] = players_df['element_type'].map(positions_map)
    players_df['form'] = pd.to_numeric(players_df['form'], errors='coerce').fillna(0)
    players_df['now_cost'] = players_df['now_cost'] / 10.0

    url_fixtures = 'https://fantasy.premierleague.com/api/fixtures/'
    fixtures_data = requests.get(url_fixtures).json()
    fixtures_df = pd.DataFrame(fixtures_data)

    return players_df, fixtures_df, teams_map, current_gw

with st.spinner("טוען נתונים חיים מה-API..."):
    players_df, fixtures_df, teams_map, current_gw = load_fpl_data()

# --- סיידבר: הגדרות ---
st.sidebar.header("⚙️ הגדרות ניתוח")
horizon_weeks = st.sidebar.select_slider("אופק תכנון (מחזורים קדימה)", options=[1, 2, 3, 4, 5], value=3)
free_transfers = st.sidebar.number_input("העברות חינמיות זמינות", min_value=1, max_value=5, value=1)

st.sidebar.markdown("---")
st.sidebar.subheader("🛡️ טעינת הקבוצה שלך")
load_method = st.sidebar.radio("שיטת טעינה:", ["FPL Official ID", "הזנה ידנית (שמות שחקנים)"])

current_squad_ids = []

if load_method == "FPL Official ID":
    team_id_input = st.sidebar.text_input("הכנס FPL Team ID:")
    if team_id_input:
        try:
            picks_url = f"https://fantasy.premierleague.com/api/entry/{team_id_input}/event/{current_gw}/picks/"
            picks_res = requests.get(picks_url).json()
            if 'picks' in picks_res:
                current_squad_ids = [p['element'] for p in picks_res['picks']]
        except:
            st.sidebar.error("שגיאה בשליפת הקבוצה.")
else:
    manual_names = st.sidebar.text_area("הדבק שמות שחקנים (מופרדים בפסיק):")
    if manual_names:
        names_list = [name.strip() for name in manual_names.split(',')]
        current_squad_ids = players_df[players_df['web_name'].isin(names_list)]['id'].tolist()

if not current_squad_ids:
    current_squad_ids = players_df.head(15)['id'].tolist()

# --- חישובים ---
max_gw = current_gw + horizon_weeks - 1
future_fixtures = fixtures_df[(fixtures_df['event'] >= current_gw) & (fixtures_df['event'] <= max_gw)].copy()

team_difficulties = {}
team_fixtures_list = {team_name: [] for team_name in teams_map.values()}

for team_id, team_name in teams_map.items():
    t_fixtures = future_fixtures[(future_fixtures['team_h'] == team_id) | (future_fixtures['team_a'] == team_id)]
    diffs = []
    for _, fix in t_fixtures.iterrows():
        opp_name = teams_map.get(fix['team_a'] if fix['team_h'] == team_id else fix['team_h'], 'Unknown')
        team_fixtures_list[team_name].append(f"{opp_name} ({'H' if fix['team_h'] == team_id else 'A'})")
        diffs.append(fix['team_h_difficulty'] if fix['team_h'] == team_id else fix['team_a_difficulty'])
            
    team_difficulties[team_name] = max(0.5, 3.5 - ((sum(diffs) / len(diffs)) * 0.5)) if diffs else 1.0

def calculate_xmins_factor(row):
    return min(1.0, max(0.3, row.get('starts', 0) / max(1, (row['minutes'] / 90))))

players_df['xmins_factor'] = players_df.apply(calculate_xmins_factor, axis=1)
merged_df = players_df[(players_df['status'] == 'a') & (players_df['xmins_factor'] > 0.2)].copy()
merged_df['xPts'] = merged_df.apply(lambda row: (row['form'] * team_difficulties.get(row['team_name'], 1.0) * row['xmins_factor']), axis=1).round(2)

# --- לוגיקת הצגה (Tabs) ---
tab1, tab2, tab3, tab4, tab5 = st.tabs(["📊 שחקנים", "📅 לוח משחקים", "👑 קפטנים", "🛡️ הסגל שלי", "🚀 אופטימיזציה"])

with tab1:
    display_lb = merged_df.sort_values(by='xPts', ascending=False)[['web_name', 'team_name', 'xPts']]
    st.dataframe(display_lb, use_container_width=True, hide_index=True)

with tab2:
    fix_df = pd.DataFrame([{'קבוצה': k, 'משחקים': ", ".join(v[:horizon_weeks])} for k, v in team_fixtures_list.items()])
    st.dataframe(fix_df, use_container_width=True, hide_index=True)

with tab3:
    captains_df = merged_df.sort_values(by='xPts', ascending=False).head(10).copy()
    captains_df['Captain xPts'] = captains_df['xPts'] * 2
    st.dataframe(captains_df[['web_name', 'xPts', 'Captain xPts']], use_container_width=True, hide_index=True)

with tab4:
    squad_df = merged_df[merged_df['id'].isin(current_squad_ids)]
    st.dataframe(squad_df[['web_name', 'team_name', 'xPts']], use_container_width=True, hide_index=True)

with tab5:
    if st.button("הפעל פותר (Solver)"):
        prob = pulp.LpProblem("FPL_Solver", pulp.LpMaximize)
        squad_vars = {i: pulp.LpVariable(f"s_{i}", cat='Binary') for i in merged_df.index}
        prob += pulp.lpSum([merged_df.loc[i, 'xPts'] * squad_vars[i] for i in merged_df.index])
        prob += pulp.lpSum([merged_df.loc[i, 'now_cost'] * squad_vars[i] for i in merged_df.index]) <= 100.0
        prob += pulp.lpSum([squad_vars[i] for i in merged_df.index]) == 15
        if prob.solve() == 1:
            st.success("הפתרון נמצא!")
            st.dataframe(merged_df[[squad_vars[i].varValue > 0.5 for i in merged_df.index]][['web_name', 'team_name']], hide_index=True)
