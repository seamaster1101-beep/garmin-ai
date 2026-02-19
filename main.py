import os
import json
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai
import requests

# --- CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
HR_MAX = 165

def format_num(val):
    """Исправляет точки на запятые и обрабатывает пустые значения"""
    if val is None or val == "" or val == 0 or val == "0": return ""
    # Если это число (float или int), конвертируем и меняем точку на запятую
    try:
        if isinstance(val, (float, int)):
            return str(val).replace('.', ',')
        return str(val).replace('.', ',')
    except:
        return str(val)

def update_or_append(sheet, date_str, row_data):
    try:
        col_values = sheet.col_values(1)
        search_date = date_str.split(' ')[0]
        found_idx = -1
        for i, val in enumerate(col_values):
            if search_date in str(val):
                found_idx = i + 1
                break
        
        formatted_row = [format_num(val) if i > 0 else val for i, val in enumerate(row_data)]
        if found_idx != -1:
            for i, val in enumerate(formatted_row[1:], start=2):
                if val != "":
                    sheet.update_cell(found_idx, i, val)
            return "Updated"
        else:
            sheet.append_row(formatted_row)
            return "Appended"
    except Exception as e:
        print(f"Sheet Error: {e}")
        return "Error"

# --- LOGIN ---
gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
gar.login()

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- ДАННЫЕ ЗДОРОВЬЯ (HRV, Пульс, BB) ---
weight, r_hr, hrv, bb_morning, slp_sc, slp_h = "", "", "", "", "", ""

try:
    # Самый надежный способ для RHR и HRV
    health_data = gar.get_rhr_and_hrv(today_str) or {}
    hrv = health_data.get("hrvSummary", {}).get("lastNightAvg", "")
    r_hr = health_data.get("restingHeartRate", "")
    
    # Если HRV всё еще нет, пробуем через stats
    if not hrv:
        stats = gar.get_stats(today_str) or {}
        hrv = stats.get("lastNightAvgHrv") or stats.get("allDayAvgHrv") or ""
    
    summary = gar.get_user_summary(today_str) or {}
    bb_morning = summary.get("bodyBatteryHighestValue") or ""

    # Сон (проверяем сегодня и вчера)
    for d in [today_str, yesterday_str]:
        s_data = gar.get_sleep_data(d)
        dto = s_data.get("dailySleepDTO") or {}
        if dto and dto.get("sleepTimeSeconds", 0) > 0:
            slp_sc = dto.get("sleepScore") or ""
            slp_h = round(dto.get("sleepTimeSeconds") / 3600, 1)
            break

    # Вес
    for i in range(3):
        d_check = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        w_data = gar.get_body_composition(d_check)
        if w_data and w_data.get('uploads'):
            weight = round(w_data['uploads'][-1].get('weight', 0) / 1000, 1)
            break
except Exception as e:
    print(f"Bio Data Error: {e}")

# --- DAILY ACTIVITY ---
try:
    daily_stats = gar.get_stats(today_str) or {}
    steps = daily_stats.get("totalSteps") or 0
    dist = round((daily_stats.get("totalDistanceMeters") or 0) / 1000, 2)
    cals = (summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0)) or daily_stats.get("calories") or 0
    bb_now = summary.get("bodyBatteryMostRecentValue") or ""
except:
    steps, dist, cals, bb_now = 0, 0, 0, ""

# --- GOOGLE SHEETS SYNC ---
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    
    # Запись в Daily и Morning
    update_or_append(ss.worksheet("Daily"), today_str, [today_str, steps, dist, cals, r_hr, bb_now])
    update_or_append(ss.worksheet("Morning"), today_str, [today_str, weight, r_hr, hrv, bb_morning, slp_sc, slp_h])
    
    # Работа с тренировками
    activities = gar.get_activities_by_date(today_str, today_str) or []
    act_sheet = ss.worksheet("Activities")
    all_rows = act_sheet.get_all_values()
    
    for a in activities:
        start = a.get('startTimeLocal', '')
        t = start.split('T')[1][:5] if 'T' in start else ""
        sp = a.get('activityType', {}).get('typeKey', 'unknown')
        
        # Проверка дубликатов по дате, времени и типу спорта
        if any(r[0] == today_str and r[1] == t and r[2] == sp for r in all_rows): continue
        
        a_hr = a.get('averageHeartRate') or a.get('averageHR') or ""
        m_hr = a.get('maxHeartRate') or a.get('maxHR') or ""
        
        inte = ""
        if a_hr:
            p = (float(a_hr) / HR_MAX) * 100
            inte = "Low" if p < 60 else "Moderate" if p < 85 else "High"

        row = [
            today_str, t, sp.capitalize(), 
            round((a.get('duration') or 0)/3600, 2), 
            round((a.get('distance') or 0)/1000, 2), 
            a_hr, m_hr, 
            a.get('trainingLoad', ''), a.get('trainingEffect', ''), 
            a.get('calories', ''), a.get('averagePower', ''), 
            a.get('averageCadence', ''), inte
        ]
        act_sheet.append_row([format_num(v) if i > 0 else v for i, v in enumerate(row)])

    # AI Advice
    advice = "Данные обновлены."
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            model = genai.GenerativeModel('gemini-1.5-flash')
            prompt = f"Биометрия: HRV {hrv}, HR {r_hr}, BB {bb_morning}, Sleep {slp_h}ч. Дай короткий ироничный совет на день."
            res = model.generate_content(prompt)
            advice = res.text.strip()
        except: pass

    # Telegram Notification
    if TELEGRAM_BOT_TOKEN:
        tg_msg = (f"📊 *Отчет {today_str}*\n\n"
                  f"👣 Шаги: {steps}\n"
                  f"💓 HRV: {hrv} | RHR: {r_hr}\n"
                  f"😴 Сон: {slp_h}ч (Score: {slp_sc})\n"
                  f"🔋 Батарейка: {bb_morning}\n\n"
                  f"🤖 {advice}")
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": tg_msg, "parse_mode": "Markdown"})

except Exception as e:
    print(f"Final Error: {e}")
