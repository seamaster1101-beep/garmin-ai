import os
import json
import time
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials
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
                    sheet.update_cell(found_idx, i, val)
            return "Updated"
        else:
            sheet.append_row(row_data)
            return "Appended"
    except Exception as e: return f"Err: {str(e)[:15]}"

# --- LOGIN ---
try:
    gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    gar.login()
except Exception as e:
    print(f"Login Fail: {e}"); exit(1)

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

# --- ИНИЦИАЛИЗАЦИЯ (Чтобы не было ошибок "not defined") ---
morning_ts = f"{today_str} 08:00"
weight, r_hr, hrv, bb_morning, slp_sc, slp_h = "", "", "", "", "", ""
daily_row = [today_str, "", "", "", "", ""]
activities_to_log = []

# --- 1. MORNING BLOCK ---
try:
    # HRV
    try:
        hrv_res = gar.get_hrv_data(today_str) or {}
        hrv = hrv_res.get("hrvSummary", {}).get("lastNightAvg") or ""
    except: pass

    # Сон
    for d in [today_str, yesterday_str]:
        try:
            sleep_data = gar.get_sleep_data(d) or {}
            dto = sleep_data.get("dailySleepDTO") or {}
            if dto and dto.get("sleepTimeSeconds", 0) > 0:
                slp_h = round(float(dto.get("sleepTimeSeconds")) / 3600, 1)
                scores = dto.get("sleepScores") or {}
                slp_sc = scores.get("overall", {}).get("value") or dto.get("sleepScore") or ""
                raw_ts = dto.get("sleepEndTimestampLocal")
                if raw_ts:
                    if isinstance(raw_ts, (int, float)):
                        morning_ts = datetime.fromtimestamp(raw_ts / 1000).strftime("%Y-%m-%d %H:%M")
                    else:
                        morning_ts = str(raw_ts).replace("T", " ")[:16]
                break
        except: continue

    # ВЕС: Сортируем по времени замера (sampleTime), чтобы не брать старье
    try:
        # Берем данные за 3 дня для надежности
        w_data = gar.get_body_composition((now - timedelta(days=3)).strftime("%Y-%m-%d"), today_str) or {}
        weights = w_data.get('dateWeightList', [])
        if weights:
            # СОРТИРОВКА: выбираем замер с самой поздней меткой времени
            actual_entry = max(weights, key=lambda x: x.get('sampleTime', 0))
            weight = round(float(actual_entry.get('weight', 0)) / 1000, 1)
            print(f"✅ Найден самый свежий вес: {weight}")
    except Exception as ew:
        print(f"DEBUG Weight Error: {ew}")

    # Сводка (Пульс, BB, Калории)
    summary = gar.get_user_summary(today_str) or {}
    r_hr = summary.get("restingHeartRate") or summary.get("heartRateRestingValue") or ""
    bb_now = summary.get("bodyBatteryMostRecentValue") or ""
    bb_morning = summary.get("bodyBatteryHighestValue") or bb_now
    
    # Заполнение Daily
    steps_data = gar.get_daily_steps(today_str, today_str)
    steps = steps_data[0].get('totalSteps', 0) if steps_data else 0
    cals = (summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0)) or 0
    daily_row = [today_str, steps, round(steps * 0.000762, 2), cals, r_hr, bb_now]

except Exception as e:
    print(f"Morning/Daily Data Collection Error: {e}")

morning_row = [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h]

# --- 2. ACTIVITIES (УЛУЧШЕННЫЙ СБОР) ---
activities_to_log = []
try:
    # Берем чуть больше активностей на случай нескольких тренировок
    latest_activities = gar.get_activities(0, 10) or []
    # Фильтруем только за сегодня и разворачиваем (от ранних к поздним)
    today_activities = [a for a in latest_activities if a.get("startTimeLocal", "").startswith(today_str)]
    today_activities.reverse() 

    for a in today_activities:
        act_id = str(a.get("activityId"))
        act_type = a.get('activityType', {}).get('typeKey', '')
        
        # Ищем IF в разных ключах (Garmin иногда меняет названия)
        if_val = a.get('intensityFactor') or a.get('weightedAverageIntensity') or ""
        if if_val: if_val = round(float(if_val), 3) # IF обычно три знака после запятой

        cad = (a.get('averageBikingCadenceInRevPerMinute') or a.get('averageBikingCadence') or "")
        t_load = round(float(a.get('activityTrainingLoad') or 0), 1)
        
        activities_to_log.append({
            "type": act_type,
            "dist": round(a.get('distance', 0) / 1000, 2),
            "speed": round(a.get('averageSpeed', 0) * 3.6, 1),
            "pwr": a.get('avgPower', "0"),
            "avg_hr": a.get('averageHR', ""),
            "max_hr": a.get('maxHR', ""),
            "aerobic": round(float(a.get('aerobicTrainingEffect', 0)), 1),
            "if": if_val,
            "id": act_id,
            "row": [
                a.get("startTimeLocal", "").replace("T", " ")[:16], 
                act_type, 
                round(a.get('duration', 0) / 3600, 2), 
                round(a.get('distance', 0) / 1000, 2),
                a.get('averageHR', ""), 
                a.get('maxHR', ""), 
                if_val, # Тот самый Intensity Factor
                t_load,
                round(float(a.get('aerobicTrainingEffect', 0)), 1), 
                a.get('calories', ""),
                a.get('avgPower', ""), 
                cad, 
                act_id
            ]
        })
except Exception as e: print(f"Activities error: {e}")

# --- 3. AI BLOCK (УМНЫЙ АНАЛИЗ ВСЕХ ТРЕНИРОВОК) ---
ai_advice = "Данные не готовы"
if GEMINI_API_KEY:
    try:
        # Автоподбор модели (оставляем твой рабочий метод)
        res_m = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}")
        models_list = res_m.json().get("models", [])
        available = [m["name"] for m in models_list if "generateContent" in m.get("supportedGenerationMethods", [])]
        target_model = next((m for m in available if "flash" in m), available[0])

        if activities_to_log:
            summary_text = ""
            for i, act in enumerate(activities_to_log, 1):
                summary_text += (f"Анализ тренировки №{i} ({act['type']}): {act['dist']} км, "
                                 f"ср.пульс {act['avg_hr']}, эффект {act['aerobic']}, "
                                 f"мощность {act['pwr']} Вт, IF: {act['if']}.\n")
            
            prompt = (f"Ты — Athlete Intelligence. Проанализируй спортивный день атлета:\n"
                      f"{summary_text}\n"
                      f"Утренние данные: HRV {hrv}, Пульс покоя {r_hr}, Сон {slp_h}ч, Score {slp_sc}.\n"
                      f"Сделай профессиональный разбор (как в Strava) каждой тренировки. "
                      f"Для силовых делай упор на пульс и восстановление, для вело — на мощность и IF. "
                      f"В конце добавь одну ироничную колкость.")
# ...
        else:
            # Если тренировок нет — обычный утренний промпт
            prompt = (f"Ты — элитный аналитик. Данные: HRV {hrv}, Пульс {r_hr}, Сон {slp_h}ч, Score {slp_sc}, BB {bb_morning}.\n"
                      f"Дай прогноз на день и ироничную колкость.")

        url = f"https://generativelanguage.googleapis.com/v1beta/{target_model}:generateContent?key={GEMINI_API_KEY}"
        res_ai = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
        ai_advice = res_ai.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e: 
        print(f"AI Error: {e}")
        ai_advice = "ИИ временно недоступен"

# --- 4. WRITE TO SHEETS & TELEGRAM ---
try:
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    credentials = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(credentials).open("Garmin_Data")
    
    # Обновляем основные листы
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    
    # Записываем активности
    act_sheet = ss.worksheet("Activities")
    existing_ids = {r[12] for r in act_sheet.get_all_values() if len(r) > 12}
    for act in activities_to_log:
        if act["id"] not in existing_ids: 
            act_sheet.append_row(act["row"])
    
    # Логируем ответ ИИ
    ss.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Info", ai_advice.replace('*', '')])
    
    # Отправка в Telegram
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            status_emoji = "🚴‍♂️+🏋️‍♂️" if len(activities_to_log) > 1 else ("🚴‍♂️" if activities_to_log else "🌅")
            header = f"{status_emoji} *Garmin Daily Analysis*\n\n"
            
            # Чистим текст для Markdown
            clean_advice = ai_advice.replace('*', '').replace('_', '')
            
            if len(clean_advice) > 3800:
                clean_advice = clean_advice[:3800] + "...\n(текст обрезан)"

            full_msg = f"{header}💓 HRV: {hrv or 'N/A'}\n🌙 Сон: {slp_h or 'N/A'}ч\n⚖️ Вес: {weight or 'N/A'}кг\n\n🤖 {clean_advice}"
            
            res = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                json={"chat_id": TELEGRAM_CHAT_ID, "text": full_msg, "parse_mode": "Markdown"}, 
                timeout=15
            )
            
            if res.status_code != 200:
                # Если Markdown упал, шлем обычным текстом
                requests.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                    json={"chat_id": TELEGRAM_CHAT_ID, "text": full_msg.replace('*', '')}, 
                    timeout=15
                )
        except Exception as te:
            print(f"Telegram Send Error: {te}")

except Exception as e:
    print(f"Final Write Error: {e}")
