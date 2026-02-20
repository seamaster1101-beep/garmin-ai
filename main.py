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
            for i, val in enumerate(row_data[1:], start=2):
                if val not in (None, "", 0, "0", 0.0, "N/A"):
                    # Cast floats to comma-separated string if needed
                    val_str = str(val).replace('.', ',') if isinstance(val, float) else str(val)
                    sheet.update_cell(found_idx, i, val_str)
            return "Updated"
        else:
            formatted_row = [str(val).replace('.', ',') if isinstance(val, float) else val for val in row_data]
            sheet.append_row(formatted_row)
            return "Appended"
    except Exception as e:
        return f"Err: {str(e)[:15]}"

def get_any(d, *keys):
    """Safely fetch the first available key from dictionary."""
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return ""

def fmt_val(v):
    """Format utility for Google Sheets (handles None, floats/commas)."""
    if v in (None, "", "None"):
        return ""
    if isinstance(v, float):
        if v.is_integer():
            return str(int(v))
        return str(round(v, 2)).replace('.', ',')
    return str(v)


# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
    print("??? Garmin login OK")
except Exception as e:
    print(f"Login Fail: {e}")
    exit(1)

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

print(f"???? Today: {today_str}")

# --- 1. MORNING BLOCK ---
morning_ts = f"{today_str} 08:00"
weight = ""
r_hr = ""
hrv = ""
bb_morning = ""
slp_sc = ""
slp_h = ""

try:
    stats = gar.get_stats(today_str) or {}
    hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or ""
    
    for d in [today_str, yesterday_str]:
        try:
            sleep_data = gar.get_sleep_data(d)
            dto = sleep_data.get("dailySleepDTO") or {}
            if dto and dto.get("sleepTimeSeconds", 0) > 0:
                slp_sc = dto.get("sleepScore") or ""
                slp_h = round(dto.get("sleepTimeSeconds", 0) / 3600, 1)
                morning_ts = dto.get("sleepEndTimeLocal", "").replace("T", " ")[:16] or morning_ts
                break
        except:
            continue

    for i in range(3):
        d_check = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            w_data = gar.get_body_composition(d_check, today_str)
            if w_data and w_data.get('uploads'):
                weight = round(w_data['uploads'][-1].get('weight', 0) / 1000, 1)
                break
        except:
            continue

    summary = gar.get_user_summary(today_str) or {}
    r_hr = summary.get("restingHeartRate") or summary.get("heartRateRestingValue") or ""
    bb_morning = summary.get("bodyBatteryHighestValue") or ""

    morning_row = [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h]
    print("??? Morning data OK")
except Exception as e:
    print(f"Morning Error: {e}")
    morning_row = [morning_ts, "", "", "", "", "", ""]

# --- 2. DAILY BLOCK ---
try:
    summary = gar.get_user_summary(today_str) or {}
    stats = gar.get_stats(today_str) or {}

    steps_data = gar.get_daily_steps(today_str, today_str)
    steps = steps_data[0].get('totalSteps', 0) if steps_data else 0

    cals = (
        summary.get("activeKilocalories", 0)
        + summary.get("bmrKilocalories", 0)
    ) or stats.get("calories") or 0

    steps_distance_km = round(steps * 0.000762, 2)

    daily_row = [
        today_str,
        steps,
        steps_distance_km,
        cals,
        r_hr,
        summary.get("bodyBatteryMostRecentValue", "")
    ]
    print("??? Daily data OK")
except Exception as e:
    print(f"Daily Error: {e}")
    daily_row = [today_str, "", "", "", "", ""]

# --- 3. ACTIVITIES BLOCK ---
activities_today = []

try:
    activities_today = gar.get_activities_by_date(today_str, today_str) or []
    print(f"??? ?????????????? ??????????????????????: {len(activities_today)}")
    
    # ?????????????????? ???? ??????????????
    def get_time(a):
        t = a.get('startTimeLocal', '')
        if 'T' in t:
            return t.split('T')[1]
        elif ' ' in t:
            return t.split(' ')[1]
        return t
    
    activities_today.sort(key=get_time)
    
    for act in activities_today:
        t = act.get('startTimeLocal', '')
        s = act.get('activityType', {}).get('typeKey', 'unknown')
        print(f"  {t} - {s}")
        
except Exception as e:
    print(f"Activities fetch error: {e}")

# --- 4. SYNC TO GOOGLE SHEETS ---
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    c_obj = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(c_obj).open("Garmin_Data")
    print("??? Google Sheets connected")
    
    # ?????????????????? ???????????????? ??????????
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    
    # --- ACTIVITIES SHEET (???????????????????? ????????????) ---
    try:
        activities_sheet = ss.worksheet("Activities")
        all_rows = activities_sheet.get_all_values()
        
        # ?????????????? ?????? ???????????? ???? ??????????????
        rows_to_delete = []
        for i, row in enumerate(all_rows[1:], start=2):
            if len(row) > 0 and row[0] == today_str:
                rows_to_delete.append(i)
        
        for row_num in reversed(rows_to_delete):
            activities_sheet.delete_rows(row_num)
            print(f"  ?????????????? ???????????? {row_num}")
        
        # ?????????????????? ?????????????????????? ????????????????????
        for activity in activities_today:
            # ???????????? ??????????
            start_time = activity.get('startTimeLocal', '')
            if 'T' in start_time:
                date_part = start_time.split('T')[0]
                time_part = start_time.split('T')[1][:5]
            elif ' ' in start_time:
                date_part = start_time.split(' ')[0]
                time_part = start_time.split(' ')[1][:5]
            else:
                date_part = today_str
                time_part = ""
            
            sport = activity.get('activityType', {}).get('typeKey', 'unknown')
            
            # ???????????????????????? ???????????? (?????????????????? ???????????????????? ?????????????????? ???? Garmin API)
            duration_sec = activity.get('duration') or 0
            duration_hr = duration_sec / 3600 if duration_sec else 0
            
            distance_m = activity.get('distance') or 0
            distance_km = distance_m / 1000 if distance_m else 0
            
            # Garmin ???????????????????? ?????????????????????? ?????????? ?????? ?????????? ??????????:
            avg_hr = get_any(activity, 'averageHR', 'averageHeartRate')
            max_hr = get_any(activity, 'maxHR', 'maxHeartRate')
            training_load = get_any(activity, 'activityTrainingLoad', 'trainingLoad')
            training_effect = get_any(activity, 'aerobicTrainingEffect', 'trainingEffect')
            calories = get_any(activity, 'calories')
            avg_power = get_any(activity, 'averagePower', 'avgPower')
            cadence = get_any(activity, 'averageRunningCadenceInStepsPerMinute', 
                                        'averageBikingCadenceInRevPerMinute', 
                                        'averageSwimCadenceInStrokesPerMinute', 
                                        'averageCadence')
            
            # ?????????????????? ??????????????, ???????? ?????? ????????
            if calories:
                try:
                    calories = int(float(calories))
                except (ValueError, TypeError):
                    pass

            # ?????????????? ???????????? (???? ?????????????? ???????????????? ?? ?????????? 13 ??????????????)
            new_row = [
                date_part,                  # 1. Date
                time_part,                  # 2. Start_Time
                sport,                      # 3. Sport
                fmt_val(duration_hr),       # 4. Duration_hr
                fmt_val(distance_km),       # 5. Distance_km
                fmt_val(avg_hr),            # 6. Avg_HR
                fmt_val(max_hr),            # 7. Max_HR
                fmt_val(training_load),     # 8. Training_Load
                fmt_val(training_effect),   # 9. Training_Effect
                fmt_val(calories),          # 10. Calories
                fmt_val(avg_power),         # 11. Avg_Power
                fmt_val(cadence),           # 12. Cadence
                ""                          # 13. HR_Intensity
            ]
            
            # value_input_option='USER_ENTERED' ?????????????????? Google Sheets ???????????????????????? ?????????? ?? ???????????????? ??????????????????
            activities_sheet.append_row(new_row, value_input_option='USER_ENTERED')
            print(f"  ??? ??????????????????: {time_part} {sport}")
        
        print(f"??? Activities: ?????????????? {len(rows_to_delete)}, ?????????????????? {len(activities_today)}")
        
    except Exception as e:
        print(f"Activities sheet error: {e}")

    # --- 5. AI ADVICE ---
    advice = "???? ???????????????? ??????!"
    
    if GEMINI_API_KEY:
        try:
            genai.configure(api_key=GEMINI_API_KEY.strip())
            model = genai.GenerativeModel('gemini-1.5-pro')
            
            acts = []
            for a in activities_today:
                sport = a.get('activityType', {}).get('typeKey', 'unknown')
                duration = round(a.get('duration', 0) / 60, 0)
                acts.append(f"{sport} {duration}??????")
            
            acts_text = ', '.join(acts) if acts else '?????? ????????????????????'
            
            prompt = (f"????????: HRV={hrv}, ??????????={r_hr}, ??????={slp_h}??. "
                      f"????????????????????: {acts_text}. ?????? ???????????????? ?????????? ???? ??????????????.")
            
            response = model.generate_content(prompt)
            if response and response.text:
                advice = f"???? {response.text.strip()}"
                print("??? AI ?????????? ??????????????")
        except Exception as ai_e:
            print(f"AI Error: {ai_e}")
    
    # --- 6. TELEGRAM ---
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            acts_list = []
            for a in activities_today:
                sport = a.get('activityType', {}).get('typeKey', 'unknown')
                duration = round(a.get('duration', 0) / 60, 0)
                acts_list.append(f"??? {sport}: {duration}??????")
            
            acts_text = '\n'.join(acts_list) if acts_list else '?????? ????????????????????'
            
            msg = (f"???? ?????????? {today_str}\n\n"
                   f"???? ??????: {slp_h}?? | HRV: {hrv}\n"
                   f"?????? ??????????: {r_hr} | ?????? ??????: {weight}????\n"
                   f"???? ????????: {steps}\n\n"
                   f"??????? ????????????????????:\n{acts_text}\n\n"
                   f"{advice}")
            
            tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN.strip()}/sendMessage"
            response = requests.post(tg_url, json={"chat_id": TELEGRAM_CHAT_ID.strip(), "text": msg}, timeout=15)
            print(f"??? Telegram ??????????????????, ????????????: {response.status_code}")
        except Exception as tg_e:
            print(f"Telegram error: {tg_e}")

    print("\n???? ????????????!")

except Exception as e:
    print(f"Final Error: {e}")
