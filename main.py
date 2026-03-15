import os, json, requests
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# --- CONFIGURATION ---
GARMIN_EMAIL = os.getenv("GARMIN_EMAIL")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
GOOGLE_CREDS_JSON = os.getenv("GOOGLE_SHEETS_CREDS")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

def update_or_append(ws, date_str, row_data):
    cells = ws.col_values(1)
    if date_str in cells:
        idx = cells.index(date_str) + 1
        for i, val in enumerate(row_data):
            if val != "": ws.update_cell(idx, i + 1, val)
    else:
        ws.append_row(row_data)

# --- 1. GARMIN DATA EXTRACTION ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
    
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # Пытаемся забрать данные, если их нет - ставим прочерк
    try: stats = gar.get_user_summary(today_str)
    except: stats = {}
    
    try: composition = gar.get_body_composition(today_str)
    except: composition = {}
    
    try: hrv_data = gar.get_hrv_data(today_str)
    except: hrv_data = {}
    
    try: sleep = gar.get_sleep_data(today_str)
    except: sleep = {}
    
    weight = round(stats.get('weight', 0) / 1000, 1) if stats.get('weight') else ""
    body_fat = composition.get('totalDailyLeaf', {}).get('bodyFat', "")
    muscle_mass = composition.get('totalDailyLeaf', {}).get('muscleMass')
    if muscle_mass: muscle_mass = round(muscle_mass / 1000, 1)

    rhr = stats.get('restingHeartRate', "")
    hrv = hrv_data.get('hrvSummary', {}).get('lastNightAvg', "") if hrv_data else ""
    bb = stats.get('bodyBatteryMostRecentValue', "")
    slp_score = sleep.get('dailySleepDTO', {}).get('score', "")
    slp_h = round(sleep.get('dailySleepDTO', {}).get('sleepTimeSeconds', 0) / 3600, 1) if sleep.get('dailySleepDTO') else ""
    
    morning_row = [today_str, weight, body_fat, muscle_mass, rhr, hrv, bb, slp_score, slp_h, "", "AI Pending"]

    # Активности
    all_acts = gar.get_activities(0, 5)
    activities_to_log = []
    
    for a in all_acts:
        start_local = a.get('startTimeLocal', '')
        if not start_local.startswith(today_str): continue
        
        act_id = a.get('activityId')
        sport = a.get('activityType', {}).get('typeKey', 'other')
        dur = round(a.get('duration', 0) / 3600, 2)
        dist = round(a.get('distance', 0) / 1000, 2)
        avg_hr = a.get('averageHR')
        max_hr = a.get('maxHR')
        
        # Мощность и TSS
        pwr = a.get('averagePower', 0)
        np = a.get('normPower', 0)
        # Если Garmin не отдал NP в списке, попробуем залезть глубже
        if not np or np == 0:
            try:
                det = gar.get_activity_details(act_id)
                np = det.get('summaryDTO', {}).get('normPower', 0)
            except: np = 0

        if_val = a.get('intensityFactor', "")
        load = a.get('trainingLoad', "")
        te = a.get('trainingEffect', "")
        cal = a.get('calories', "")
        cad = a.get('averageCadence', "")
        tss = a.get('trainingStressScore', "")
        vi = round(np / pwr, 2) if np and pwr and pwr > 0 else ""

        row = [start_local, sport, dur, dist, avg_hr, max_hr, if_val, load, te, cal, pwr, cad, np, tss, vi, str(act_id)]
        activities_to_log.append({"id": str(act_id), "row": row})

    # --- 2. AI ANALYSIS ---
    genai.configure(api_key=GEMINI_API_KEY)
    # ИСПОЛЬЗУЕМ НОВУЮ МОДЕЛЬ FLASH 2.0
    model = genai.GenerativeModel('gemini-2.0-flash')
    
    prompt = f"""
    Ты Athlete Intelligence. Твой атлет: Вес {weight}кг, Жир {body_fat}%, Мышцы {muscle_mass}кг.
    Утро: HRV {hrv}, Пульс {rhr}, Сон {slp_h}ч (Score {slp_score}), Батарея {bb}.
    Тренировки сегодня: {[{'тип': x['row'][1], 'нагрузка': x['row'][7], 'NP': x['row'][12]} for x in activities_to_log]}
    
    Задачи:
    1. Рассчитай Fitness Age по своей методике (HRV vs Состав тела).
    2. Оцени готовность к 50км велозаезду сегодня.
    3. Дай ироничный совет.
    """
    response = model.generate_content(prompt)
    ai_advice = response.text

    # --- 3. WRITE TO SHEETS ---
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    credentials = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(credentials).open("Garmin_Data")
    
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    
    act_sheet = ss.worksheet("Activities")
    existing_ids = {r[15] for r in act_sheet.get_all_values() if len(r) > 15}
    for act in activities_to_log:
        if act["id"] not in existing_ids: 
            act_sheet.append_row(act["row"])
    
    ss.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Report", ai_advice])

    # --- 4. TELEGRAM ---
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        clean_advice = ai_advice.replace('*', '')
        full_msg = f"📊 *Athlete Report*\n💓 HRV: {hrv}\n⚖️ Жир: {body_fat}%\n\n{clean_advice[:3800]}"
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": full_msg, "parse_mode": "Markdown"})

except Exception as e:
    print(f"Критическая ошибка: {e}")
