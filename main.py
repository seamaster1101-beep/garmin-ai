import os
import json
from datetime import datetime
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

# --- 1. CONFIG ---
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
# Проверяем оба варианта имени секрета
creds_json = os.environ.get("GOOGLE_CREDS") or os.environ.get("GOOGLE_SHEETS_CREDS")

def update_or_append(sheet, date_str, row_data):
    try:
        col_values = sheet.col_values(1)
        search_date = date_str.split(' ')[0]
        found_idx = -1
        for i, val in enumerate(col_values):
            if search_date in str(val):
                found_idx = i + 1
                break
        if found_idx != -1:
            # Обновляем ячейки со 2-й колонки (B)
            for i, val in enumerate(row_data[1:], start=2):
                if val not in (None, "", 0, "0", 0.0):
                    sheet.update_cell(found_idx, i, val)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except Exception as e:
        print(f"Sheet update error: {e}")

# --- 2. GARMIN DATA ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
    today = datetime.now().strftime("%Y-%m-%d")

    # Сбор данных
    stats = gar.get_user_summary(today)
    sleep = gar.get_sleep_data(today)
    hrv_data = gar.get_hrv_data(today) or {}
    
    # Извлекаем калории (пробуем разные ключи для надежности)
    cals = stats.get('totalCalories') or stats.get('caloriesOutAllDay') or ""
    
    # Время пробуждения (07:22)
    dto = sleep.get('dailySleepDTO', {})
    wake_time = dto.get('sleepEndTimeLocal', "").replace('T', ' ')[:16]
    if not wake_time: wake_time = datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # Сон и HRV
    slp_h = round(dto.get('sleepTimeSeconds', 0) / 3600, 1) if dto.get('sleepTimeSeconds') else ""
    slp_score = dto.get('sleepScore', "")
    hrv_val = hrv_data.get('hrvSummary', {}).get('lastNightAvg', "")
    
    # Вес (берем последний доступный)
    weight = ""
    try:
        # Просим данные за сегодня
        w_body = gar.get_body_composition(today, today)
        w_list = w_body.get('dateWeightList', [])
        if w_list:
            weight = round(w_list[-1]['weight'] / 1000, 1)
    except: pass

    # --- 3. MORNING ROW (Строгий порядок A-K) ---
    # A:Date, B:Weight, C:Fat, D:Muscle, E:R_HR, F:HRV, G:BB, H:Score, I:Hours, J:Age, K:FitAge
    morning_row = [
        wake_time,                  # A
        str(weight).replace('.',','),# B
        "",                         # C (Fat)
        "",                         # D (Muscle)
        stats.get('restingHeartRate', ""), # E
        hrv_val,                    # F
        stats.get('bodyBatteryHighestValue', ""), # G
        slp_score,                  # H
        str(slp_h).replace('.',','),# I
        62,                         # J (Age)
        "AI Analysis"               # K
    ]

    # --- 4. DAILY ROW (A-F) ---
    dist = round(stats.get('totalDistanceMeters', 0) / 1000, 2)
    daily_row = [
        today,                      # A
        stats.get('totalSteps', 0), # B
        str(dist).replace('.',','), # C
        cals,                       # D (Calories)
        stats.get('restingHeartRate', ""), # E
        stats.get('bodyBatteryMostRecentValue', "") # F
    ]

    # --- 5. GOOGLE SHEETS WRITE ---
    if not creds_json:
        raise ValueError("GOOGLE_CREDS not found!")
    
    info = json.loads(creds_json)
    creds = Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    client = gspread.authorize(creds)
    ss = client.open("Garmin_Data")

    update_or_append(ss.worksheet("Morning"), today, morning_row)
    update_or_append(ss.worksheet("Daily"), today, daily_row)

    print(f"SUCCESS: Wake {wake_time}, Calories {cals}")

except Exception as e:
    print(f"CRITICAL ERROR: {e}")
