import os
import json
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

print("🚀 Starting Garmin → Google Sheets PRO (Smart Update Edition)")

# ---------- SETTINGS ----------
HR_MAX = 165
gemini_key = os.environ.get("GEMINI_API_KEY")

# ---------- FUNCTIONS ----------
def update_or_append(sheet, date_str, row_data):
    """Ищет дату в первом столбце. Если находит — обновляет строку, если нет — добавляет."""
    try:
        cell = sheet.find(date_str)
        row_num = cell.row
        # Обновляем только те ячейки, которые не пусты в row_data
        # Начинаем со 2-го столбца (индекс 1 в row_data, индекс 2 в Sheets)
        for i, value in enumerate(row_data[1:], start=2):
            if value != "" and value is not None:
                sheet.update_cell(row_num, i, value)
        print(f"✅ Данные в листе '{sheet.title}' за {date_str} обновлены.")
    except gspread.exceptions.CellNotFound:
        sheet.append_row(row_data)
        print(f"✅ В лист '{sheet.title}' добавлена новая строка за {date_str}.")

# ---------- GARMIN LOGIN ----------
email = os.environ["GARMIN_EMAIL"]
password = os.environ["GARMIN_PASSWORD"]

client = Garmin(email, password)
client.login()

now = datetime.now()
today_date = now.strftime("%Y-%m-%d")
print(f"Fetching data for: {today_date}")

# ---------- DATA COLLECTION ----------
# 1. Основная статистика
stats = client.get_stats(today_date)
steps = stats.get("totalSteps") or 0
daily_calories = stats.get("totalKilocalories") or 0
raw_dist = stats.get("totalDistanceMeters") or 0
daily_distance_km = round(raw_dist / 1000, 2)
resting_hr = stats.get("restingHeartRate") or ""
body_battery = stats.get("bodyBatteryMostRecentValue") or ""

# 2. Вес
try:
    body_data = client.get_body_composition(today_date)
    weight = round(body_data.get('totalWeight', 0) / 1000, 1) if body_data and body_data.get('totalWeight') else ""
except: weight = ""

# 3. HRV
try:
    hrv_data = client.get_hrv_data(today_date)
    hrv = hrv_data[0].get('lastNightAvg', "") if hrv_data and len(hrv_data) > 0 else ""
except: hrv = ""

# 4. Сон
try:
    sleep_data = client.get_sleep_data(today_date)
    sleep_score = sleep_data.get('dailySleepDTO', {}).get('sleepScore', "")
    s_sec = sleep_data.get('dailySleepDTO', {}).get('sleepTimeSeconds') or 0
    sleep_min = round(s_sec / 60, 0) if s_sec > 0 else ""
except: sleep_score, sleep_min = "", ""

# 5. Последняя активность
try:
    activities = client.get_activities(0, 1)
    last_act = activities[0] if activities and activities[0]['startTimeLocal'].startswith(today_date) else None
except: last_act = None

# ---------- AI ANALYSIS ----------
ai_advice = "Анализ не выполнен"
if gemini_key:
    try:
        genai.configure(api_key=gemini_key.strip())
        model = genai.GenerativeModel('gemini-1.5-flash') # Прямой вызов модели
        
        workout_info = f"Тренировка: {last_act['activityType']['typeKey']}, TE: {last_act.get('trainingEffect')}" if last_act else "Тренировок не было"
        user_prompt = (f"Проанализируй показатели за сегодня ({today_date}): "
                       f"Сон: {sleep_score}/100, HRV: {hrv}, Пульс покоя: {resting_hr}, "
                       f"Body Battery: {body_battery}, Шаги: {steps}. {workout_info}. "
                       f"Дай краткую оценку восстановления и совет на завтра (2 предложения).")
        
        response = model.generate_content(user_prompt)
        ai_advice = response.text
    except Exception as e:
        ai_advice = f"AI Error: {str(e)[:50]}"

# ---------- GOOGLE SHEETS SYNC ----------
creds_dict = json.loads(os.environ["GOOGLE_CREDS"])
credentials = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
gc = gspread.authorize(credentials)
spreadsheet = gc.open("Garmin_Data")

# Обновляем Daily
daily_data = [today_date, steps, daily_distance_km, daily_calories, resting_hr, body_battery]
update_or_append(spreadsheet.worksheet("Daily"), today_date, daily_data)

# Обновляем Morning
morning_data = [today_date, weight, resting_hr, hrv, body_battery, sleep_score, sleep_min]
update_or_append(spreadsheet.worksheet("Morning"), today_date, morning_data)

# Обновляем Activities (только если есть новая)
if last_act:
    act_sheet = spreadsheet.worksheet("Activities")
    avg_hr = last_act.get('averageHR', 0)
    te = last_act.get('trainingEffect', 0)
    hr_intensity = round(avg_hr / HR_MAX, 2) if avg_hr else ""
    
    # Чтобы не дублировать активности, проверяем время старта (столбец B)
    start_time = last_act['startTimeLocal'][11:16]
    existing_times = act_sheet.col_values(2)
    if start_time not in existing_times:
        act_sheet.append_row([
            today_date, start_time, last_act['activityType']['typeKey'].capitalize(),
            round(last_act['duration'] / 3600, 2), round(last_act.get('distance', 0) / 1000, 2),
            avg_hr, last_act.get('maxHR', ""), last_act.get('trainingLoad', ""),
            te, last_act.get('calories', ""), last_act.get('avgPower', ""),
            last_act.get('averageRunningCadence', ""), hr_intensity, "Session"
        ])

# Пишем в лог
spreadsheet.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Sync Success", ai_advice])

print("✅ Финиш! Таблица актуализирована.")
