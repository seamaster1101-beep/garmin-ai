import os, json, requests
from datetime import datetime, timedelta
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
            ws.update_cell(idx, i + 1, val)
    else:
        ws.append_row(row_data)

# --- 1. GARMIN DATA EXTRACTION ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
    
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    
    # Биометрия (S2 и Профиль)
    stats = gar.get_user_summary(today_str)
    composition = gar.get_body_composition(today_str)
    hrv_data = gar.get_hrv_data(today_str)
    sleep = gar.get_sleep_data(today_str)
    
    weight = round(stats.get('weight', 0) / 1000, 1) if stats.get('weight') else ""
    # Данные с весов S2
    body_fat = composition.get('totalDailyLeaf', {}).get('bodyFat')
    muscle_mass = composition.get('totalDailyLeaf', {}).get('muscleMass')
    if muscle_mass: muscle_mass = round(muscle_mass / 1000, 1)

    rhr = stats.get('restingHeartRate')
    hrv = hrv_data.get('hrvSummary', {}).get('lastNightAvg') if hrv_data else ""
    bb = stats.get('bodyBatteryMostRecentValue')
    slp_score = sleep.get('dailySleepDTO', {}).get('score')
    slp_h = round(sleep.get('dailySleepDTO', {}).get('sleepTimeSeconds', 0) / 3600, 1)
    
    # Morning Row (A-K): Date, Weight, Fat, Muscle, RHR, HRV, BB, SlpScore, SlpH, Age, FitnessAge
    morning_row = [today_str, weight, body_fat, muscle_mass, rhr, hrv, bb, slp_score, slp_h, "", "AI Pending"]

    # Активности
    all_acts = gar.get_activities(0, 5)
    activities_to_log = []
    
    for a in all_acts:
        start_local = a.get('startTimeLocal', '')
        if not start_local.startswith(today_str): continue
        
        act_id = a.get('activityId')
        # Детальные метрики (NP, TSS)
        details = gar.get_activity_details(act_id)
        summary = details.get('summaryDTO', {})
        
        # Сбор данных
        sport = a.get('activityType', {}).get('typeKey', 'other')
        dur = round(a.get('duration', 0) / 3600, 2)
        dist = round(a.get('distance', 0) / 1000, 2)
        avg_hr = a.get('averageHR')
        max_hr = a.get('maxHR')
        
        # IF и Load
        rel_int = a.get('relativeIntensity') or a.get('weightedAverageIntensity')
        if_val = a.get('intensityFactor') or (float(rel_int)/100 if rel_int else "")
        load = a.get('trainingLoad')
        te = a.get('trainingEffect')
        cal = a.get('calories')
        pwr = a.get('averagePower')
        cad = a.get('averageCadence')
        
        # Вело-метрики
        np = summary.get('normPower')
        tss = summary.get('trainingStressScore')
        vi = round(np / pwr, 2) if np and pwr and pwr > 0 else ""

        # Activities Row (A-P): Date, Sport, Dur, Dist, AvgHR, MaxHR, IF, Load, TE, Cal, Pwr, Cad, NP, TSS, VI, ID
        row = [start_local, sport, dur, dist, avg_hr, max_hr, if_val, load, te, cal, pwr, cad, np, tss, vi, str(act_id)]
        activities_to_log.append({"id": str(act_id), "row": row, "data": a})

    # --- 2. AI ANALYSIS ---
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-pro')
    
    prompt = f"""
    Ты - Athlete Intelligence. Твой атлет: Вес {weight}кг, Жир {body_fat}%, Мышцы {muscle_mass}кг.
    Утро: HRV {hrv}, Пульс покоя {rhr}, Сон {slp_h}ч (балл {slp_score}), Батарея {bb}.
    Тренировки сегодня: {[{'тип': x['row'][1], 'нагрузка': x['row'][7], 'NP': x['row'][12]} for x in activities_to_log]}
    
    Задачи:
    1. Рассчитай Fitness Age по своей методике (сравни HRV и состав тела).
    2. Проанализируй баланс нагрузки и восстановления.
    3. Оцени вело-метрики (NP/TSS), если они есть.
    4. Дай ироничный, но глубокий совет.
    """
    ai_advice = model.generate_content(prompt).text

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
        try:
            clean_advice = ai_advice.replace('*', '').replace('_', '')
            full_msg = f"📊 *Athlete Report*\n💓 HRV: {hrv}\n⚖️ Fat: {body_fat}%\n🤖 {clean_advice[:3800]}"
            requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                          json={"chat_id": TELEGRAM_CHAT_ID, "text": full_msg, "parse_mode": "Markdown"}, timeout=15)
        except: pass

except Exception as e:
    print(f"Error: {e}")
