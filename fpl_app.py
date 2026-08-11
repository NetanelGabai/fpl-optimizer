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

# --- פונקציית טעינת נתונים עם Caching לייעול מהירות ---
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

with st.spinner("טוען נתונים חיים מה-API של הפנטזי..."):
    players_df, fixtures_df, teams_map, current_gw = load_fpl_data()

# --- סיידבר: הגדרות גלובליות ושליפת קבוצה אמיתית ---
st.sidebar.header("⚙️ הגדרות ניתוח וניהול")

horizon_weeks = st.sidebar.select_slider("אופק תכנון (מחזורים קדימה)", options=[1, 2, 3, 4, 5], value=3)
free_transfers = st.sidebar.number_input("העברות חינמיות זמינות", min_value=1, max_value=5, value=1)

st.sidebar.markdown("---")
st.sidebar.subheader("🛡️ הקבוצה שלך מ-FPL")
team_id_input = st.sidebar.text_input("הכנס את ה-FPL Team ID שלך:", value="")

# --- עיבוד נתונים וחישובים ---
max_gw = current_gw + horizon_weeks - 1
future_fixtures = fixtures_df[(fixtures_df['event'] >= current_gw) & (fixtures_df['event'] <= max_gw)].copy()

team_difficulties = {}
team_fixtures_list = {team_name: [] for team_name in teams_map.values()}

for team_id, team_name in teams_map.items():
    t_fixtures = future_fixtures[(future_fixtures['team_h'] == team_id) | (future_fixtures['team_a'] == team_id)]
    diffs = []
    for _, fix in t_fixtures.iterrows():
        if fix['team_h'] == team_id:
            diffs.append(fix['team_h_difficulty'])
            opponent_id = fix['team_a']
            opp_name = teams_map.get(opponent_id, 'Unknown')
            team_fixtures_list[team_name].append(f"{opp_name} (H)")
        else:
            diffs.append(fix['team_a_difficulty'])
            opponent_id = fix['team_h']
            opp_name = teams_map.get(opponent_id, 'Unknown')
            team_fixtures_list[team_name].append(f"{opp_name} (A)")
            
    if diffs:
        avg_diff = sum(diffs) / len(diffs)
        team_difficulties[team_name] = max(0.5, 3.5 - (avg_diff * 0.5))
    else:
        team_difficulties[team_name] = 1.0

def calculate_xmins_factor(row):
    total_mins_possible = row['minutes'] + 1 
    if total_mins_possible <= 90: 
        return 1.0 if row['status'] == 'a' else 0.0
    starts = row.get('starts', 0)
    return min(1.0, max(0.3, starts / max(1, (row['minutes'] / 90))))

players_df['xmins_factor'] = players_df.apply(calculate_xmins_factor, axis=1)
merged_df = players_df[(players_df['status'] == 'a') & (players_df['xmins_factor'] > 0.2)].copy()

def calculate_advanced_xpts(row):
    base = row['form'] if row['form'] > 0 else (row['now_cost'] / 2.0)
    fixture_mult = team_difficulties.get(row['team_name'], 1.0)
    xmins = row['xmins_factor']
    pos = row['position']
    cs_prob = min(0.6, max(0.1, fixture_mult * 0.2))
    attacking_threat = base * fixture_mult * xmins
    
    if pos in ['Defender', 'Goalkeeper']:
        return attacking_threat + (cs_prob * 4.0) + 1.2
    elif pos == 'Midfielder':
        return (attacking_threat * 1.1) + (cs_prob * 1.0) + 0.6
    elif pos == 'Forward':
        return (attacking_threat * 1.2) + 0.2
    return attacking_threat

merged_df['xPts'] = merged_df.apply(calculate_advanced_xpts, axis=1).round(2)
merged_df = merged_df.reset_index(drop=True)

# משיכת הסגל האמיתי לפי Team ID אם הוזן
current_squad_ids = []
if team_id_input:
    try:
        picks_url = f"https://fantasy.premierleague.com/api/entry/{team_id_input}/event/{current_gw}/picks/"
        picks_res = requests.get(picks_url).json()
        if 'picks' in picks_res:
            current_squad_ids = [p['element'] for p in picks_res['picks']]
    except:
        st.sidebar.error("שגיאה בשליפת הקבוצה. בדוק את ה-Team ID.")

if not current_squad_ids:
    current_squad_ids = merged_df['id'].head(15).tolist()

# --- יצירת לשוניות ניווט (Tabs) ---
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "📊 שחקנים מובילים", 
    "📅 לוח משחקים וקושי", 
    "👑 מטריצת קפטנים", 
    "🛡️ הסגל שלי", 
    "🚀 מנוע אופטימיזציה"
])

# --- TAB 1: שחקנים מובילים ---
with tab1:
    st.subheader("🔥 טבלת שחקנים מובילים לפי תוחלת נקודות (xPts)")
    pos_filter = st.selectbox("סינון לפי עמדה:", ["הכל", "Goalkeeper", "Defender", "Midfielder", "Forward"])
    
    display_lb = merged_df.copy()
    if pos_filter != "הכל":
        display_lb = display_lb[display_lb['position'] == pos_filter]
        
    display_lb = display_lb.sort_values(by='xPts', ascending=False)[['web_name', 'team_name', 'position', 'now_cost', 'form', 'xPts']]
    display_lb.columns = ['שחקן', 'קבוצה', 'עמדה', 'מחיר (M)', 'כושר (Form)', 'xPts (באופק)']
    st.dataframe(display_lb.head(25), use_container_width=True, hide_index=True)

# --- TAB 2: לוח משחקים וקושי קבוצתי ---
with tab2:
    st.subheader(f"📅 5 המשחקים הבאים לכל קבוצה ומדד הקושי (החל מחזור {current_gw})")
    
    fixtures_table = []
    for t_name, fixes in team_fixtures_list.items():
        fixtures_table.append({
            'קבוצה': t_name,
            'משחקים קרובים': ", ".join(fixes[:horizon_weeks]),
            'ציון נוחות ל"ווליו"': round(team_difficulties.get(t_name, 1.0), 2)
        })
    
    fix_df = pd.DataFrame(fixtures_table).sort_values(by='ציון נוחות ל"ווליו"', ascending=False)
    st.dataframe(fix_df, use_container_width=True, hide_index=True)

# --- TAB 3: מטריצת קפטנים ---
with tab3:
    st.subheader("👑 מטריצת קפטנים מומלצים למחזור הקרוב")
    st.markdown("השחקנים המובילים ביותר למחזור הקרוב עם חישוב פוטנציאל ניקוד כפול (Captain xPts).")
    
    captains_df = merged_df.sort_values(by='xPts', ascending=False).head(10).copy()
    captains_df['Captain xPts'] = captains_df['xPts'] * 2
    captains_display = captains_df[['web_name', 'team_name', 'position', 'now_cost', 'form', 'xPts', 'Captain xPts']]
    captains_display.columns = ['שחקן', 'קבוצה', 'עמדה', 'מחיר (M)', 'כושר (Form)', 'xPts רגיל', 'xPts כקפטן (©)']
    st.dataframe(captains_display, use_container_width=True, hide_index=True)

# --- TAB 4: הסגל הנוכחי שלי ---
with tab4:
    st.subheader("🛡️ הסגל שלך בפנטזי")
    if team_id_input:
        st.success(f"הקבוצה נטענה בהצלחה עבור Team ID: {team_id_input}")
    else:
        st.info("הכנס את ה-Team ID שלך בסיידבר כדי לראות את הקבוצה האמיתית שלך. בינתיים מוצגת סביבת הדגמה.")
        
    my_squad_df = merged_df[merged_df['id'].isin(current_squad_ids)]
    squad_display = my_squad_df[['web_name', 'team_name', 'position', 'now_cost', 'xPts']]
    squad_display.columns = ['שחקן', 'קבוצה', 'עמדה', 'מחיר (M)', 'xPts']
    st.dataframe(squad_display, use_container_width=True, hide_index=True)

# --- TAB 5: מנוע האופטימיזציה (הסולבר) ---
with tab5:
    st.subheader("🚀 הרצת אלגוריתם אופטימיזציה לסגל שלך")
    run_solver = st.button("הפעל פותר (Solver)")
    
    if run_solver:
        with st.spinner("מריץ חישוב לינארי להרכב האופטימלי..."):
            prob = pulp.LpProblem("FPL_Pro_Solver", pulp.LpMaximize)
            squad_vars = {i: pulp.LpVariable(f"squad_{i}", cat='Binary') for i in merged_df.index}
            starter_vars = {i: pulp.LpVariable(f"starter_{i}", cat='Binary') for i in merged_df.index}
            captain_vars = {i: pulp.LpVariable(f"captain_{i}", cat='Binary') for i in merged_df.index}
            transfer_in_vars = {i: pulp.LpVariable(f"transfer_in_{i}", cat='Binary') for i in merged_df.index}
            hits_var = pulp.LpVariable("hits", lowBound=0, cat='Integer')

            prob += pulp.lpSum([
                merged_df.loc[i, 'xPts'] * starter_vars[i] + 
                merged_df.loc[i, 'xPts'] * captain_vars[i] for i in merged_df.index
            ]) - (4.0 * hits_var), "Total_Net_Expected_Points"

            prob += pulp.lpSum([merged_df.loc[i, 'now_cost'] * squad_vars[i] for i in merged_df.index]) <= 100.0
            prob += pulp.lpSum([squad_vars[i] for i in merged_df.index]) == 15
            prob += pulp.lpSum([squad_vars[i] for i in merged_df.index if merged_df.loc[i, 'position'] == 'Goalkeeper']) == 2
            prob += pulp.lpSum([squad_vars[i] for i in merged_df.index if merged_df.loc[i, 'position'] == 'Defender']) == 5
            prob += pulp.lpSum([squad_vars[i] for i in merged_df.index if merged_df.loc[i, 'position'] == 'Midfielder']) == 5
            prob += pulp.lpSum([squad_vars[i] for i in merged_df.index if merged_df.loc[i, 'position'] == 'Forward']) == 3

            for team in merged_df['team_name'].unique():
                prob += pulp.lpSum([squad_vars[i] for i in merged_df.index if merged_df.loc[i, 'team_name'] == team]) <= 3

            for i in merged_df.index:
                prob += starter_vars[i] <= squad_vars[i]
                prob += captain_vars[i] <= starter_vars[i]

            prob += pulp.lpSum([starter_vars[i] for i in merged_df.index]) == 11
            prob += pulp.lpSum([captain_vars[i] for i in merged_df.index]) == 1
            prob += pulp.lpSum([starter_vars[i] for i in merged_df.index if merged_df.loc[i, 'position'] == 'Goalkeeper']) == 1
            prob += pulp.lpSum([starter_vars[i] for i in merged_df.index if merged_df.loc[i, 'position'] == 'Defender']) >= 3
            prob += pulp.lpSum([starter_vars[i] for i in merged_df.index if merged_df.loc[i, 'position'] == 'Midfielder']) >= 2
            prob += pulp.lpSum([starter_vars[i] for i in merged_df.index if merged_df.loc[i, 'position'] == 'Forward']) >= 1

            for i in merged_df.index:
                is_in_current = 1 if merged_df.loc[i, 'id'] in current_squad_ids else 0
                prob += transfer_in_vars[i] >= squad_vars[i] - is_in_current

            prob += hits_var >= pulp.lpSum([transfer_in_vars[i] for i in merged_df.index]) - free_transfers
            prob.solve()

            if prob.status == 1:
                starters, bench, transfers_in = [], [], []
                for i in merged_df.index:
                    if squad_vars[i].varValue and squad_vars[i].varValue > 0.5:
                        p_data = merged_df.loc[i].copy()
                        if p_data['id'] not in current_squad_ids:
                            transfers_in.append(p_data['web_name'])
                        if captain_vars[i].varValue and captain_vars[i].varValue > 0.5:
                            p_data['web_name'] = f"© {p_data['web_name']}"
                            p_data['xPts'] *= 2
                        
                        d_dict = {'שחקן': p_data['web_name'], 'קבוצה': p_data['team_name'], 'עמדה': p_data['position'], 'מחיר (M)': p_data['now_cost'], 'xPts': p_data['xPts']}
                        if starter_vars[i].varValue and starter_vars[i].varValue > 0.5:
                            starters.append(d_dict)
                        else:
                            bench.append(d_dict)

                st.success("האופטימיזציה הסתיימה בהצלחה!")
                col_main, col_bench = st.columns([2, 1])
                with col_main:
                    st.subheader("🌟 הרכב פותח מומלץ")
                    st.dataframe(pd.DataFrame(starters), use_container_width=True, hide_index=True)
                with col_bench:
                    st.subheader("🪑 ספסל")
                    st.dataframe(pd.DataFrame(bench), use_container_width=True, hide_index=True)
                
                if transfers_in:
                    st.info(f"🔄 שחקנים מומלצים לרכישה: {', '.join(transfers_in)}")
                else:
                    st.info("🔄 אין צורך בהעברות, הסגל הנוכחי אופטימלי.")
            else:
                st.error("לא נמצא פתרון תחת האילוצים.")
