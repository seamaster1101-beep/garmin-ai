import os
from datetime import datetime, timedelta

import gspread
from telegram import Bot
import google.genai as genai
from garminconnect import Garmin

# === Настройка Garmin Connect ===
gar = Garmin(os.environ['GARMIN_EMAIL'], os.environ['GARMIN_PASSWORD'])
gar.login()

# === Настройка Google Sheets ===
gc = gspread.service_account(filename=os.environ['GOOGLE_CREDS'])
sh = gc.open("Garmin Data")  # Имя вашей таблицы
ws = sh.sheet1

# === Настройка Telegram ===
bot = Bot(token=os.environ['TELEGRAM_BOT_TOKEN'])
chat_id = os.environ['TELEGRAM_CHAT_ID']

# === Настройка Google Gemini ===
genai.configure(api_key=os.environ['GEMINI_API_KEY'])

# === MORNING BLOCK ===
today_str = datetime.now().strftime("%Y-%m-%d")
yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
now = datetime.now()

morning_ts = f"{today_str} 08:00"
weight = r_hr = hrv = bb_morning = slp_sc = slp_h = ""

try:
    # --- HRV ---
    stats = gar.get_stats(today_str) or {}
    hrv = stats.get("allDayAvgHrv") or stats.get("lastNightAvgHrv") or stats.get("lastNightHrv") or stats.get("morningHrv") or ""
    try: hrv = round(float(hrv), 1) if hrv != "" else ""
    except: hrv = ""

    # --- Sleep ---
    for d in [today_str, yesterday_str]:
        try:
            sleep_data = gar.get_sleep_data(d)
            dto = sleep_data.get("dailySleepDTO") or {}
            if dto and dto.get("sleepTimeSeconds", 0) > 0:
                slp_h = round(dto.get("sleepTimeSeconds", 0)/3600, 1)
                slp_sc = dto.get("sleepScore") or sleep_data.get("sleepScore") or sleep_data.get("score") or round(slp_h*10)
                try: slp_sc = int(slp_sc)
                except: slp_sc = ""
                morning_ts = dto.get("sleepEndTimeLocal", "").replace("T"," ")[:16] or morning_ts
                break
        except: continue

    # --- Weight за последние 7 дней ---
    for i in range(7):
        d_check = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            w_data = gar.get_body_composition(d_check, today_str)
            if w_data and w_data.get('uploads'):
                weight_val = w_data['uploads'][-1].get('weight', 0)
                if weight_val > 0:
                    weight = round(weight_val/1000,1)
                    break
        except: continue

    # fallback на weight из summary
    summary = gar.get_user_summary(today_str) or {}
    if not weight:
        weight_summary = summary.get("weight") or summary.get("weightKg") or 0
        if weight_summary > 0:
            weight = round(float(weight_summary), 1)

    # --- Resting HR и Body Battery ---
    r_hr = summary.get("restingHeartRate") or summary.get("heartRateRestingValue") or 0
    r_hr = r_hr if r_hr>0 else ""
    bb_morning = summary.get("bodyBatteryHighestValue") or 0
    bb_morning = bb_morning if bb_morning>0 else ""

    # --- HRV из последних активностей если пусто ---
    if not hrv:
        try:
            activities = gar.get_activities(0,5)
            for act in activities:
                act_type = act.get("activityType","").lower()
                if "run" in act_type or "ride" in act_type:
                    act_detail = gar.get_activity(act["activityId"])
                    hrv_val = act_detail.get("hrv")
                    if hrv_val:
                        hrv = round(float(hrv_val),1)
                        break
        except: pass

    # --- Формируем строку для записи ---
    morning_row = [morning_ts, weight, r_hr, hrv, bb_morning, slp_sc, slp_h]

    # --- 1️⃣ Запись в Google Sheets ---
    ws.append_row(morning_row)

    # --- 2️⃣ Отправка в Telegram ---
    msg = f"🚀 Утренние данные:\n" \
          f"🕗 Время: {morning_ts}\n" \
          f"⚖️ Вес: {weight}\n" \
          f"❤️ Пульс: {r_hr}\n" \
          f"💓 HRV: {hrv}\n" \
          f"🔋 Body Battery: {bb_morning}\n" \
          f"🛌 Сон: {slp_h}ч, Score: {slp_sc}"
    bot.send_message(chat_id=chat_id, text=msg)

    # --- 3️⃣ Пример запроса к AI (Gemini) ---
    ai_prompt = f"Проанализируй утренние данные: {morning_row}"
    ai_response = genai.chat.create(
        model="gemini-1.5",
        messages=[{"role":"user", "content": ai_prompt}]
    )
    ai_text = ai_response.choices[0].message.content
    bot.send_message(chat_id=chat_id, text=f"🤖 AI: {ai_text}")

except Exception as e:
    print(f"Morning Block Error: {e}")
    bot.send_message(chat_id=chat_id, text=f"❌ Ошибка MORNING BLOCK: {e}")
