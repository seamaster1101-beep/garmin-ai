import os
import json
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
import google.generativeai as genai

# ---------- НАСТРОЙКИ ----------
# Извлекаем ключи из переменных окружения
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")

# ---------- ФУНКЦИИ ----------
def update_or_append(sheet, date_str, row_data):
    """Обновляет строку, если дата уже есть, или добавляет новую."""
    try:
        dates = sheet.col_values(1)
        if date_str in dates:
            row_num = dates.index(date_str) + 1
            # Обновляем ячейки со 2-й колонки (Weight и далее)
            for i, new_value in enumerate(row_data[1:], start=2):
                if new_value != "" and new_value is not None:
                    sheet.update_cell(row_num, i, new_value)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except Exception as e:
        print(f"Error syncing with Sheets: {e}")
        return f"Error: {e}"

# ---------- ЛОГИН В GARMIN ----------
client = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
client.login()

now = datetime.now()
today_date = now.strftime("%Y-%m-%d")
yesterday_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# ---------- СБОР ДАННЫХ ----------
debug_log = []

# 1. Общие показатели (Пульс и Батарейка)
stats = client.get_stats(today_date)
resting_hr = stats.get("restingHeartRate") or ""
body_battery = stats.get("bodyBatteryMostRecentValue") or ""

# 2. ВЕС (Ищем в разных местах API)
weight = ""
try:
    # Пробуем за сегодня и вчера для надежности
    w_data = client.get_body_composition(yesterday_date, today_date)
    if w_data and 'uploads' in w_data and w_data['uploads']:
        # Берем самый свежий замер из списка
        last_w = w_data['uploads'][-1]
        weight = round(last_w.get('weight', 0) / 1000, 1)
        debug_log.append(f"W:Found({weight})")
    else:
        # Запасной метод
        weight = round(w_data.get('totalWeight', 0) / 1000,
