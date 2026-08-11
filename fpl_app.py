import streamlit as st
import requests
import pandas as pd
import pulp

# --- הגדרות תצוגה ועיצוב הממשק ---
st.set_page_config(page_title="ליגה לרציניים בלבד - Pro Optimizer", layout="wide", page_icon="⚽")

st.markdown("""
    <style>
    h1 { color: #FFD700; text-shadow: 2px 2px #000000; text-align: center; padding-bottom: 20px;}
    .stDataFrame { border: 2px solid #000000; border-radius: 5px; }
    </style>
""", unsafe_allow_html=True)

st.title("🏆 ליגה לרציניים בלבד - מנוע אופטימיזציה Pro (Multi-GW & xMins)")

# --- סיידבר: הגדרות ואפשרויות ---
st.sidebar.header("הגדרות מתקדמות")
horizon_weeks = st.sidebar.slider("אופק תכנון (מחזורים קדימה)", min_value=1, max_value=5, value=3)
free_transfers = st.sidebar.number_input("העברות חינמיות זמינות", min_value=1, max_value=5, value=1)
run_optimization = st.sidebar.button("🚀 הרץ אופטימיזציה חכמה")

if run_optimization:
    with st.spinner("שואב נתונים חיים מה-API של FPL ומנתח לוח משחקים קדימה..."):
        
        # 1. משיכת נתונים סטטיים ולוח משחקים
        url_static = 'https://fantasy.premierleague.com/api/bootstrap-static/'
        data_static = requests.get(url_static).json()

        teams_map = {team['id']: team['name'] for team in data_static['teams']}
        positions_map = {pos['id']: pos['singular_name'] for pos in data_static['element_types']}
        
        # זיהוי המחזור הקרוב ביותר כמספר בודד (תיקון השגיאה)
        events_df = pd.DataFrame(data_static['events'])
        next_gw_rows = events_df[events_df['is_next'] == True]
        if not next_gw_rows.empty:
            current_gw = int(next_gw_rows['id'].values[0])
        else:
            current_gw = 1 

        players_df = pd.DataFrame(data_static['elements'])
        players_df['team_name'] = players_df['team'].map(teams_map)
        players_df['position'] = players_df['element_type'].map(positions_map)
        players_df['form'] = pd.to_numeric(players_df['form'], errors='coerce').fillna(0)
        players_df['now_cost'] = players_df['now_cost'] / 10.0

        # 2. חישוב קושי משחקים רב-מחזורי (Multi-GW Fixture Difficulty)
        url_fixtures = 'https://fantasy.premierleague.com/api/fixtures/'
        fixtures_data = requests.get(url_fixtures).json()
        fixtures_df = pd.DataFrame(fixtures_data)

        max_gw = current_gw + horizon_weeks - 1
        future_fixtures = fixtures_df[(fixtures_df['event'] >= current_gw) & (fixtures_df['event'] <= max_gw)].copy()

        team_difficulties = {}
        for team_id, team_name in teams_map.items():
            home_games = future_fixtures[future_fixtures['team_h'] == team_id]['team_h_difficulty']
            away_games = future_fixtures[future_fixtures['team_a'] == team_id]['team_a_difficulty']
            all_diffs = pd.concat([home_games, away_games])
            
            if not all_diffs.empty:
                avg_diff = all_diffs.mean()
                team_difficulties[team_name] = max(0.5, 3.5 - (avg_diff * 0.5))
            else:
                team_difficulties[team_name] = 1.0

        # 3. מודל דקות צפויות (xMins Factor)
        def calculate_xmins_factor(row):
            total_mins_possible = row['minutes'] + 1 
            if total_mins_possible <= 90: 
                return 1.0 if row['status'] == 'a' else 0.0
            starts = row.get('starts', 0)
            history_factor = min(1.0, max(0.3, starts / max(1, (row['minutes'] / 90))))
            return history_factor

        players_df['xmins_factor'] = players_df.apply(calculate_xmins_factor, axis=1)
        merged_df = players_df[(players_df['status'] == 'a') & (players_df['xmins_factor'] > 0.2)].copy()

        # 4. מודל ה-xPts המתקדם
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

        # 5. פותר האופטימיזציה Pro
        current_squad_ids = merged_df['id'].head(15).tolist() 

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

        prob += pulp.lpSum([merged_df.loc[i, 'now_cost'] * squad_vars[i] for i in merged_df.index]) <= 100.0, "Budget"
        prob += pulp.lpSum([squad_vars[i] for i in merged_df.index]) == 15, "Total_Squad_15"

        prob += pulp.lpSum([squad_vars[i] for i in merged_df.index if merged_df.loc[i, 'position'] == 'Goalkeeper']) == 2
        prob += pulp.lpSum([squad_vars[i] for i in merged_df.index if merged_df.loc[i, 'position'] == 'Defender']) == 5
        prob += pulp.lpSum([squad_vars[i] for i in merged_df.index if merged_df.loc[i, 'position'] == 'Midfielder']) == 5
        prob += pulp.lpSum([squad_vars[i] for i in merged_df.index if merged_df.loc[i, 'position'] == 'Forward']) == 3

        for team in merged_df['team_name'].unique():
            prob += pulp.lpSum([squad_vars[i] for i in merged_df.index if merged_df.loc[i, 'team_name'] == team]) <= 3

        for i in merged_df.index:
            prob += starter_vars[i] <= squad_vars[i]
            prob += captain_vars[i] <= starter_vars[i]

        prob += pulp.lpSum([starter_vars[i] for i in merged_df.index]) == 11, "Starters_11"
        prob += pulp.lpSum([captain_vars[i] for i in merged_df.index]) == 1, "Captain_1"
        prob += pulp.lpSum([starter_vars[i] for i in merged_df.index if merged_df.loc[i, 'position'] == 'Goalkeeper']) == 1
        prob += pulp.lpSum([starter_vars[i] for i in merged_df.index if merged_df.loc[i, 'position'] == 'Defender']) >= 3
        prob += pulp.lpSum([starter_vars[i] for i in merged_df.index if merged_df.loc[i, 'position'] == 'Midfielder']) >= 2
        prob += pulp.lpSum([starter_vars[i] for i in merged_df.index if merged_df.loc[i, 'position'] == 'Forward']) >= 1

        for i in merged_df.index:
            is_in_current = 1 if merged_df.loc[i, 'id'] in current_squad_ids else 0
            prob += transfer_in_vars[i] >= squad_vars[i] - is_in_current

        total_transfers = pulp.lpSum([transfer_in_vars[i] for i in merged_df.index])
        prob += hits_var >= total_transfers - free_transfers

        prob.solve()

        # 6. הצגת התוצאות בממשק
        if prob.status == 1:
            starters, bench, transfers_in = [], [], []
            for i in merged_df.index:
                if squad_vars[i].varValue and squad_vars[i].varValue > 0.5:
                    player_data = merged_df.loc[i].copy()
                    
                    if player_data['id'] not in current_squad_ids:
                        transfers_in.append(player_data['web_name'])
                        
                    if captain_vars[i].varValue and captain_vars[i].varValue > 0.5:
                        player_data['web_name'] = f"© {player_data['web_name']}"
                        player_data['xPts'] = player_data['xPts'] * 2
                        
                    display_dict = {
                        'שחקן': player_data['web_name'],
                        'קבוצה': player_data['team_name'],
                        'עמדה': player_data['position'],
                        'מחיר (M)': player_data['now_cost'],
                        'xPts (אופק)': player_data['xPts']
                    }
                    if starter_vars[i].varValue and starter_vars[i].varValue > 0.5:
                        starters.append(display_dict)
                    else:
                        bench.append(display_dict)

            starters_df = pd.DataFrame(starters).sort_values(by=['position', 'xPts (אופק)'], ascending=[True, False])
            bench_df = pd.DataFrame(bench).sort_values(by=['position', 'מחיר (M)'], ascending=[True, True])

            total_cost = starters_df['מחיר (M)'].sum() + bench_df['מחיר (M)'].sum()
            total_xpts = starters_df['xPts (אופק)'].sum()
            hits_taken = int(hits_var.varValue)

            st.success("האופטימיזציה הרב-מחזורית הושלמה בהצלחה!")
            
            col1, col2, col3 = st.columns(3)
            col1.metric("תוחלת נקודות נטו (באופק)", f"{total_xpts - (hits_taken * 4):.2f}")
            col2.metric("תקציב מנוצל", f"{total_cost:.1f}M")
            col3.metric("מינוסים (Hits)", str(hits_taken))

            st.markdown("---")
            
            col_main, col_bench = st.columns([2, 1])
            with col_main:
                st.subheader(f"🌟 הרכב פותח (תחזית ל-{horizon_weeks} מחזורים קדימה)")
                st.dataframe(starters_df, use_container_width=True, hide_index=True)
                
            with col_bench:
                st.subheader("🪑 ספסל")
                st.dataframe(bench_df, use_container_width=True, hide_index=True)
                
            if transfers_in:
                st.info(f"🔄 **שחקנים מומלצים לרכישה:** {', '.join(transfers_in)}")
            else:
                st.info("🔄 **אין העברות:** האלגוריתם ממליץ להמשיך עם הסגל הקיים.")

        else:
            st.error("❌ המנוע לא הצליח למצוא פתרון אופטימלי תחת האילוצים שהוגדרו.")
else:
    st.info("👈 בחר את אופק התכנון בסיידבר ולחץ על 'הרץ אופטימיזציה חכמה' כדי לקבל את הסגל שלך.")
