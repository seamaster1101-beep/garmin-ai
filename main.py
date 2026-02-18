import os
import json
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# --- CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")

def safe_val(v):
    return v if v not in (None, "", 0, "0", "None") else ""

def update_or_append(sheet, date_str, row_data):
    try:
        col_values = sheet.col_values(1)
        if date_str in col_values:
            row_idx = col_values.index(date_str) + 1
            for i, val in enumerate(row_data[1:], start=2):
                if val != "":
                    sheet.update_cell(row_idx, i, val)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except Exception as e:
        return f"Err: {str(e)[:30]}"

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except Exception as e:
    print("Login fail"); exit(1)

now = datetime.now()
today = now.strftime("%Y-%m-%d")
yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- DATA ---
# 1. Stats
try:
    stats = gar.get_stats(today) or {}
    hr = safe_val(stats.get("restingHeartRate"))
    bb = safe_val(stats.get("bodyBatteryMostRecentValue"))
except: hr = bb = ""

# 2. Weight (Fix syntax)
weight = ""
try:
    w_data = gar.get_body_composition(today, today)
    if w_data and w_data.get('uploads'):
        weight = round(w_data['uploads'][-1].get('weight', 0) / 1000, 1)
    else:
        summary = gar.get_user_summary(today)
        if summary.get('weight'):
            weight = round(summary['weight'] / 1000, 1)
except: weight = ""

# 3. HRV
hrv = ""
try:
    h_data = gar.get_hrv_data(today) or gar.get_hrv_data(yesterday)
    if h_data: hrv = safe_val(h_data[0].get("lastNightAvg"))
except: hrv = ""

# 4. Sleep
slp_score = ""; slp_hours = ""
try:
    s = gar.get_sleep_data(today)
    d = s.get("dailySleepDTO") or s.get("daily_sleep_dto") or {}
    slp_score = safe_val(d.get("sleepScore"))
    sec = d.get("sleepTimeSeconds", 0)
    if sec > 0: slp_hours = round(sec/3600, 1)
except: pass

# --- AI ---
advice = "No advice"
if GEMINI_API_KEY:
    try:
        genai.configure(api_key=GEMINI_API_KEY.strip())
        model_list = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        m_name = "models/gemini-1.5-pro" if "models/gemini-1.5-pro" in model_list else model_list[0]
        model = genai.GenerativeModel(m_name)
        prompt = f"Данные: Сон {slp_hours}ч (Score {slp_score}), HRV {hrv}, HR {hr}, BB {bb}, Вес {weight}. Дай совет на завтра (2 фразы)."
        advice = model.generate_content(prompt).text.strip()
    except Exception as e: advice = f"AI Error: {str(e)[:50]}"

# --- SHEETS ---
try:
    creds = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    # Morning update
    m_status = update_or_append(ss.worksheet("Morning"), today, [today, weight, hr, hrv, bb, slp_score, slp_hours])
    
    # Clean Log
    ss.worksheet("AI_Log").append_row([
        datetime.now().strftime("%Y-%m-%d %H:%M"),
        f"Status: {m_status} | W:{weight} | HRV:{hrv}",
        advice
    ])
    print("Done!")
except Exception as e: print(f"Sheets error: {e}")
