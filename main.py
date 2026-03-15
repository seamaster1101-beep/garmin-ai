import os, json, requests
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# --- CONFIG ---
GARMIN_EMAIL = os.getenv("GARMIN_EMAIL")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_SHEETS_CREDS")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def update_or_append(ws, date_key, row_data):
    try:
        cells = ws.col_values(1)
        date_only = date_key[:10]
        found_idx = -1
        for i, val in enumerate(cells):
            if val.startswith(date_only):
                found_idx = i + 1
                break
        
        if found_idx > 0:
            for i, val in enumerate(row_data):
                if val is not None and val != "":
                    ws.update_cell(found_idx, i + 1, val)
        else:
            ws.append_row(row_data)
    except Exception as e: print(f"Sheets error: {e}")

try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # 1. Сбор данных
    stats = gar.get_user_summary(today_str)
    try: comp = gar.get_body_composition(today_str)
    except: comp = {}
    try: hrv_data = gar.get_hrv_data(today_str)
    except: hrv_data = {}
    try: sleep = gar.get_sleep_data(today_str)
    except: sleep = {}

    wake_time = sleep.get('dailySleepDTO', {}).get('sleepEndTimeLocal')
    if wake_time:
        display_time = datetime.fromisoformat(wake_time).strftime("%Y-%m-%d %H:%M")
    else:
        display_time = datetime.now().strftime("%Y-%m-%d %H:%M")

    weight = round(stats.get('weight', 0) / 1000, 1) if stats.get('weight') else ""
    fat = comp.get('totalDailyLeaf', {}).get('bodyFat', "")
    muscle = comp.get('totalDailyLeaf', {}).get('muscleMass')
    if muscle: muscle = round(muscle / 1000, 1)
    
    rhr = stats.get('restingHeartRate', "")
    hrv = hrv_data.get('hrvSummary', {}).get('lastNightAvg', "")
    bb = stats.get('bodyBatteryMostRecentValue', "")
    slp_score = sleep.get('dailySleepDTO', {}).get('score', "")
    slp_h = round(sleep.get('dailySleepDTO', {}).get('sleepTimeSeconds', 0) / 3600, 1) if sleep.get('dailySleepDTO') else ""
    
    user_settings = gar.get_user_settings()
    birth_date = user_settings.get('birthDate', '1984-01-01')
    age = datetime.now().year - int(birth_date[:4])

    morning_row = [display_time, weight, fat, muscle, rhr, hrv, bb, slp_score, slp_h, age, "AI Calculation"]

    # 2. Активности
    all_acts = gar.get_activities(0, 5)
    activities_to_log = []
    for a in all_acts:
        start = a.get('startTimeLocal', '')
        if not start.startswith(today_str): continue
        a_id = a.get('activityId')
        try:
            det = gar.get_activity_details(a_id)
            summary = det.get('summaryDTO', {})
            np = summary.get('normPower', "")
            tss = summary.get('trainingStressScore', "")
        except:
            np, tss = "", ""

        row = [start, a.get('activityType', {}).get('typeKey'), 
               round(a.get('duration', 0)/3600, 2), round(a.get('distance', 0)/1000, 2),
               a.get('averageHR'), a.get('maxHR'), a.get('intensityFactor'),
               a.get('trainingLoad'), a.get('trainingEffect'), a.get('calories'),
               a.get('averagePower'), a.get('averageCadence'), np, tss, "", str(a_id)]
        activities_to_log.append({"id": str(a_id), "row": row})

    # --- ЗАПИСЬ В ТАБЛИЦУ ---
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(creds).open("Garmin_Data")
    
    update_or_append(ss.worksheet("Morning"), display_time, morning_row)
    
    act_ws = ss.worksheet("Activities")
    # Проверка на наличие данных в таблице перед получением ID
    all_rows = act_ws.get_all_values()
    exist = {r[15] for r in all_rows if len(r) > 15}
    for act in activities_to_log:
        if act["id"] not in exist: act_ws.append_row(act["row"])
    
    # 3. AI
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        prompt = f"Атлет {age} лет. Вес {weight}, Жир {fat}%, Мышцы {muscle}. HRV {hrv}, Сон {slp_h}ч. Едет 50км вел. Оцени его Fitness Age и дай совет."
        res = model.generate_content(prompt)
        ai_advice = res.text
        
        ss.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Report", ai_advice])
        if TELEGRAM_BOT_TOKEN:
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                          json={"chat_id": TELEGRAM_CHAT_ID, "text": f"🚵‍♂️ *Ride Ready*\n\n{ai_advice}", "parse_mode": "Markdown"})
    except Exception as ai_e: print(f"AI Error: {ai_e}")

except Exception as e: print(f"Global Error: {e}")
