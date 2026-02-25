import os
import json
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
from google import genai  # Используем актуальный SDK
import requests

# --- CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def clean(val):
    """Преобразование для Google Таблиц (точки в запятые)"""
    if val is None or val == "" or val == 0: return ""
    if isinstance(val, str): val = val.replace('.', ',')
    return str(val).replace('.', ',')

def update_or_append(sheet, date_str, row_data):
    try:
        col_values = sheet.col_values(1)
        search_date = date_str.split(' ')[0]
        found_idx = -1
        for i, val in enumerate(col_values):
            if search_date in val:
                found_idx = i + 1
                break
        if found_idx != -1:
            # Обновляем существующую строку
            for i, val in enumerate(row_data[1:], start=2):
                if val not in (None, "", 0, "0", 0.0, "N/A"): 
                    sheet.update_cell(found_idx, i, clean(val))
            return "Updated"
        else:
            # Добавляем новую
            sheet.append_row([clean(x) for x in row_data])
            return "Appended"
    except Exception as e: return f"Err: {str(e)[:15]}"

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
    print("✅ Garmin Login Success")
except Exception as e:
    print(f"❌ Login Fail: {e}"); exit(1)

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- 1. DATA COLLECTION ---
morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h = f"{today_str} 08:00", "", "", "", "", "", ""
steps, cals, dist = 0, 0, 0

try:
    stats = gar.get_stats(today_str) or {}
    hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or ""
    
    # Ищем данные о сне (сегодня или вчера)
    for d in [today_str, yesterday_str]:
        try:
            sleep_data = gar.get_sleep_data(d)
            dto = sleep_data.get("dailySleepDTO") or {}
            if dto and dto.get("sleepTimeSeconds", 0) > 0:
                slp_sc = dto.get("sleepScore") or ""
                slp_h = round(dto.get("sleepTimeSeconds", 0) / 3600, 1)
                morning_ts = dto.get("sleepEndTimeLocal", "").replace("T", " ")[:16]
                break
        except: continue

    summary = gar.get_user_summary(today_str) or {}
    r_hr = summary.get("restingHeartRate") or ""
    bb_morning = summary.get("bodyBatteryHighestValue") or ""
    
    steps_data = gar.get_daily_steps(today_str, today_str)
    steps = steps_data[0].get('totalSteps', 0) if steps_data else 0
    cals = summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0)
    dist = round(steps * 0.000762, 2)

    try:
        w_data = gar.get_body_composition(today_str)
        if w_data and w_data.get('uploads'):
            weight = round(w_data['uploads'][-1].get('weight', 0) / 1000, 1)
    except: pass

except Exception as e:
    print(f"⚠️ Data Collection Error: {e}")

# --- 2. AI ADVICE (Gemini 1.5 Flash) ---
advice = "Совет временно недоступен"
if GEMINI_API_KEY:
    try:
        # Убедимся, что используем самую стабильную модель
        client = genai.Client(api_key=GEMINI_API_KEY.strip())
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=f"HRV {hrv}, HR {r_hr}, Sleep {slp_h}h. Дай мудрый совет на русском."
        )
        if response and response.text:
            advice = response.text.strip()
        else:
            advice = "Тело спит, и ИИ спит."
    except Exception as ai_e:
        print(f"DEBUG: Полная ошибка AI: {ai_e}")
        advice = "Слушай своё тело, а не сломанный ИИ."

# --- 3. WRITE TO GOOGLE SHEETS ---
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds_dict, scopes=[
        "https://www.googleapis.com/auth/spreadsheets", 
        "https://www.googleapis.com/auth/drive"
    ])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    update_or_append(ss.worksheet("Daily"), today_str, [today_str, steps, dist, cals, r_hr, bb_morning])
    update_or_append(ss.worksheet("Morning"), today_str, [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h])
    
    ss.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Success", advice])
    print("✅ Google Sheets Updated")

except Exception as e:
    print(f"❌ Sheets Error: {e}")

# --- 4. TELEGRAM ---
if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
    try:
        msg = (f"🚀 *Garmin Report {today_str}*\n\n"
               f"👣 Шаги: {steps}\n"
               f"🌙 Сон: {slp_h}ч (Score: {slp_sc})\n"
               f"💓 HRV: {hrv}\n\n"
               f"🤖 *Совет:* {advice.replace('*', '')}")
        
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        print("✅ Telegram Sent")
    except:
        print("⚠️ Telegram Fail")
