import os
import json
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

print("🚀 Starting Garmin → Google Sheets PRO (Final Polished Edition)")

# ---------- SETTINGS ----------
HR_MAX = 165
gemini_key = os.environ.get("GEMINI_API_KEY")

# ---------- FUNCTIONS ----------
def update_or_append(sheet, date_str, row_data):
    """Ищет дату. Если находит — обновляет только непустые ячейки, если нет — добавляет."""
    try:
        cell = sheet.find(date_str)
        row_num = cell.row
        # Читаем текущую строку, чтобы не затереть данные старыми пустыми значениями
        current_values = sheet.row_values(row_num)
        
        for i, new_value in enumerate(row_data[1:], start=2):
            # Обновляем, если новое значение не пустое
            if new_value != "" and new_value is not None:
                sheet.update_cell(row_num, i, new_value)
        print(f"✅ Лист '{sheet.title}': данные за {date_str} дополнены.")
    except gspread.exceptions.CellNotFound:
        sheet.append_row(row_data)
        print(f"✅ Лист '{sheet.title}': создана новая строка за {date_str}.")

# ---------- GARMIN LOGIN ----------
email = os.environ["GARMIN_EMAIL"]
password = os.environ["GARMIN_PASSWORD"]

client = Garmin(email, password)
client.login()

now = datetime.now()
today_date = now.strftime("%Y-%m-%d")

# ---------- DATA COLLECTION ----------
stats = client.get_stats(today_date)
steps = stats.get("totalSteps") or 0
daily_calories = stats.get("totalKilocalories") or 0
daily_distance_km = round((stats.get("totalDistanceMeters") or 0) / 1000, 2)
resting_hr = stats.get("restingHeartRate") or ""
body_battery = stats.get("bodyBatteryMostRecentValue") or ""

# Вес
try:
    body_data = client.get_body_composition(today_date)
    weight = round(body_data.get('totalWeight', 0) / 1000, 1) if body_data and body_data.get('totalWeight') else ""
except: weight = ""

# HRV
try:
    hrv_data = client.get_hrv_data(today_date)
    hrv = hrv_data[0].get('lastNightAvg', "") if hrv_data else ""
except: hrv = ""

# Сон (теперь в часах)
try:
    sleep_data = client.get_sleep_data(today_date)
    sleep_score = sleep_data.get('dailySleepDTO', {}).get('sleepScore', "")
    s_sec = sleep_data.get('dailySleepDTO', {}).get('sleepTimeSeconds') or 0
    # Переводим в часы, например 7.5
    sleep_hours = round(s_sec / 3600, 1) if s_sec > 0 else ""
except: sleep_score, sleep_hours = "", ""

# Активность
try:
    activities = client.get_activities(0, 1)
    last_act = activities[0] if activities and activities[0]['startTimeLocal'].startswith(today_date) else None
except: last_act = None

# ---------- AI ANALYSIS (Fixed 404) ----------
ai_advice = "Анализ недоступен"
if gemini_key:
    try:
        genai.configure(api_key=gemini_key.strip())
        # Исправлено: пробуем прямое имя модели без префикса
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        workout_info = f"Тренировка: {last_act['activityType']['typeKey']}" if last_act else "Тренировок не было"
        user_prompt = (f"Данные за {today_date}: Сон {sleep_hours}ч (Score: {sleep_score}), HRV: {hrv}, "
                       f"Пульс покоя: {resting_hr}, Body Battery: {body_battery}, Шаги: {steps}. {workout_info}. "
                       f"Дай краткий совет на завтра (2 фразы).")
        
        response = model.generate_content(user_prompt)
        ai_advice = response.text
    except Exception as e:
        ai_advice = f"AI Error: {str(e)[:100]}"

# ---------- GOOGLE SHEETS SYNC ----------
creds_dict = json.loads(os.environ["GOOGLE_CREDS"])
credentials = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
gc = gspread.authorize(credentials)
spreadsheet = gc.open("Garmin_Data")

# 1. Daily
update_or_append(spreadsheet.worksheet("Daily"), today_date, [today_date, steps, daily_distance_km, daily_calories, resting_hr, body_battery])

# 2. Morning (теперь Sleep_Hours вместо Sleep_Minutes)
# Важно: переименуй колонку в самой таблице Google на "Sleep_Hours"
update_or_append(spreadsheet.worksheet("Morning"), today_date, [today_date, weight, resting_hr, hrv, body_battery, sleep_score, sleep_hours])

# 3. Activities
if last_act:
    act_sheet = spreadsheet.worksheet("Activities")
    start_time = last_act['startTimeLocal'][11:16]
    # Простая проверка на дубли по времени
    if start_time not in act_sheet.col_values(2):
        avg_hr = last_act.get('averageHR', 0)
        act_sheet.append_row([
            today_date, start_time, last_act['activityType']['typeKey'].capitalize(),
            round(last_act['duration'] / 3600, 2), round(last_act.get('distance', 0) / 1000, 2),
            avg_hr, last_act.get('maxHR', ""), last_act.get('trainingLoad', ""),
            last_act.get('trainingEffect', ""), last_act.get('calories', ""), "", "", 
            round(avg_hr/HR_MAX, 2) if avg_hr else "", "Session"
        ])

# 4. Log
spreadsheet.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Auto-Sync", ai_advice])

print(f"✅ Данные успешно синхронизированы. Сон: {sleep_hours}ч.")
