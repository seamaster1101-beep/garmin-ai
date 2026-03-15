import os
import json
import requests
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

# --- CONFIG ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
# Теперь переменная GOOGLE_CREDS_JSON точно содержит твой секрет
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_SHEETS_CREDS")

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
            sheet.update(f"A{found_idx}", [row_data], value_input_option='USER_ENTERED')
        else:
            sheet.append_row(row_data, value_input_option='USER_ENTERED')
    except Exception as e: print(f"Err: {e}")

# --- LOGIN ---
gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
gar.login()
now = datetime.now()
today_str = now.strftime("%Y-%m-%d")

# --- 1. ПЕРВИЧНЫЕ ДАННЫЕ (Объявляем всё здесь) ---
summary = gar.get_user_summary(today_str) or {}
hrv_res = gar.get_hrv_data(today_str) or {}
hrv = hrv_res.get("hrvSummary", {}).get("lastNightAvg") or ""
r_hr = summary.get("restingHeartRate") or ""

# --- 2. ВЕС, ЖИР, МЫШЦЫ (На основе твоего лога S2) ---
weight, fat, muscle = "", "", ""
try:
    w_data = gar.get_body_composition((now - timedelta(days=3)).strftime("%Y-%m-%d"), today_str) or {}
    weights = w_data.get('dateWeightList', [])
    if weights:
        # Берем самый свежий замер (за 15.03)
        actual_entry = max(weights, key=lambda x: x.get('sampleTime', x.get('date', 0)))
        
        # Вес: 88080.0 -> 88.1
        weight = round(float(actual_entry.get('weight', 0)) / 1000, 1)
        
        # Жир: 18.3
        fat = actual_entry.get('bodyFat', "")
        
        # Мышцы: 32500 -> 32.5
        raw_m = actual_entry.get('muscleMass')
        if raw_m:
            muscle = round(float(raw_m) / 1000, 1)
except Exception as e:
    print(f"Ошибка парсинга весов: {e}")

# --- 3. СОН И ВРЕМЯ (Твой рабочий утренний алгоритм) ---
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
morning_ts = f"{today_str} 08:00"
slp_sc, slp_h = "", ""

for d in [today_str, yesterday_str]:
    try:
        sleep_data = gar.get_sleep_data(d) or {}
        dto = sleep_data.get("dailySleepDTO") or {}
        if dto and dto.get("sleepTimeSeconds", 0) > 0:
            slp_h = round(float(dto.get("sleepTimeSeconds")) / 3600, 1)
            # Тот самый поиск Score, который у тебя работал
            scores = dto.get("sleepScores") or {}
            slp_sc = scores.get("overall", {}).get("value") or dto.get("sleepScore") or ""
            
            raw_ts = dto.get("sleepEndTimestampLocal")
            if raw_ts:
                if isinstance(raw_ts, (int, float)):
                    morning_ts = datetime.fromtimestamp(raw_ts / 1000).strftime("%Y-%m-%d %H:%M")
                else:
                    morning_ts = str(raw_ts).replace("T", " ")[:16]
            break # Нашли данные — выходим из цикла
    except:
        continue

# --- 4. FITNESS AGE (Логика на основе биомаркеров Garmin) ---
fit_age = ""
try:
    actual_age = 62
    # 1. Влияние пульса покоя (RHR)
    # Garmin хвалит за 45-46. Если RHR <= 48, это отличный бонус.
    rhr_val = int(r_hr) if r_hr else 60
    rhr_impact = (rhr_val - 55) * 0.4  # Чем ниже 55, тем моложе
    
    # 2. Влияние жира (Body Fat)
    # У тебя 18.3%, что для 62 лет — атлетический уровень.
    fat_val = float(fat) if fat else 25
    fat_impact = (fat_val - 22) * 0.5  # Норма около 22%, всё что ниже — молодит
    
    # 3. Влияние HRV (Косвенный маркер стресса и восстановления)
    hrv_val = int(hrv) if hrv else 40
    hrv_impact = (hrv_val - 45) * 0.1  # Высокий HRV — признак молодого сердца
    
    # Итоговый расчет
    calculated = actual_age + rhr_impact + fat_impact - hrv_impact
    
    # Ограничиваем разумными пределами (как у Garmin)
    fit_age = round(max(45, min(actual_age + 5, calculated)), 1)
except:
    fit_age = "62"
        
# --- 5. ФОРМИРОВАНИЕ СТРОК ---
# A:Date(1), B:Weight(2), C:Fat(3), D:Muscle(4), E:RHR(5), F:HRV(6), G:BB(7), H:Score(8), I:Hours(9), J:Age(10), K:FitAge(11)
morning_row = [
    f"'{morning_ts}", 
    weight, 
    fat,        # Теперь здесь данные из actual_entry.get('bodyFat')
    muscle,     # Теперь здесь данные из actual_entry.get('muscleMass')
    r_hr, 
    hrv, 
    summary.get("bodyBatteryHighestValue", ""), 
    slp_sc, 
    slp_h, 
    62, 
    fit_age
]

steps = summary.get('totalSteps', 0)
cals = int(summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0))
daily_row = [f"'{today_str}", steps, round(steps * 0.000762, 2), cals, r_hr, summary.get("bodyBatteryMostRecentValue", "")]

# --- 2. ACTIVITIES (Финальная сборка под твои столбцы A-P) ---
activities_to_log = []
try:
    # Берем последние 5, чтобы не перегружать память
    latest_activities = gar.get_activities(0, 5) or []
    
    for a in latest_activities:
        start_local = a.get("startTimeLocal", "")
        # Оставляем только СЕГОДНЯ (фильтр на 15.03)
        if not start_local.startswith(today_str): 
            continue
        
        act_id = str(a.get("activityId"))
        
        # Вытягиваем сложные метрики мощности
        np_val = a.get('normPower') or a.get('weightedAveragePower', "")
        if_val = a.get('intensityFactor')
        tss_val = a.get('trainingStressScore')
        avg_pwr = a.get('avgPower', "")
        
        # Считаем VI (Индекс вариативности)
        vi_val = ""
        if np_val and avg_pwr and float(avg_pwr) > 0:
            vi_val = round(float(np_val) / float(avg_pwr), 2)

        # Твои столбцы один в один:
        row_data = [
            start_local.replace("T", " ")[:16],      # A: Дата
            a.get('activityType', {}).get('typeKey', ''), # B: Вид спорта
            round(a.get('duration', 0) / 3600, 2),   # C: Длительность (час)
            round(a.get('distance', 0) / 1000, 2),   # D: Дистанция (км)
            a.get('averageHR', ""),                  # E: Средний пульс
            a.get('maxHR', ""),                      # F: Макс пульс
            round(float(if_val), 3) if if_val else "", # G: IF (Intensity Factor)
            round(float(a.get('activityTrainingLoad', 0)), 1), # H: Load (Нагрузка)
            round(float(a.get('aerobicTrainingEffect', 0)), 1), # I: TE (Эффект)
            a.get('calories', ""),                   # J: Калории
            avg_pwr,                                 # K: Ср. Мощность
            a.get('averageBikingCadenceInRevPerMinute') or a.get('averageBikingCadence') or "", # L: Каденс
            round(float(np_val), 1) if np_val else "", # M: NP (Норм. мощность)
            round(float(tss_val), 1) if tss_val else "", # N: TSS
            vi_val,                                  # O: VI (Вариативность)
            act_id                                   # P: ID (для проверки дублей)
        ]
        
        activities_to_log.append({"id": act_id, "row": row_data})
except Exception as e:
    print(f"Activity Error: {e}")
# --- 6. ЗАПИСЬ ---
creds_dict = json.loads(GOOGLE_CREDS_JSON)
ss = gspread.authorize(Credentials.from_service_account_info(creds_dict, 
     scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])).open("Garmin_Data")

update_or_append(ss.worksheet("Morning"), today_str, morning_row)
update_or_append(ss.worksheet("Daily"), today_str, daily_row)

print(f"✅ Финиш: Время={morning_ts}, Вес={weight}, Score={slp_sc}, Calories={cals}")

# --- 3. AI BLOCK (Берем данные из morning_row) ---
ai_advice = "ИИ анализирует..."
if GEMINI_API_KEY:
    try:
        # 1. Подбор модели
        res_m = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}")
        available = [m["name"] for m in res_m.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
        target_model = next((m for m in available if "flash" in m), available[0])

        # 2. Промпт (Берем данные из morning_row по индексам столбцов)
        # Индексы из твоего morning_row: 5=HRV, 4=Пульс, 8=Сон, 7=Score, 6=BodyBattery, 10=FitAge
        prompt = (f"Ты — элитный аналитик здоровья. Разбери показатели: "
                  f"HRV {morning_row[5]}, Пульс {morning_row[4]}, Сон {morning_row[8]}ч, "
                  f"Body Battery {morning_row[6]}. "
                  f"Фитнес-возраст {morning_row[10]} при реальном 62 года! "
                  f"Дай краткий прогноз и одну колкую ироничную шутку.")

        # 3. Запрос к Gemini
        url = f"https://generativelanguage.googleapis.com/v1beta/{target_model}:generateContent?key={GEMINI_API_KEY}"
        res_ai = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
        ai_advice = res_ai.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
        
    except Exception as e:
        print(f"AI Error: {e}")
        ai_advice = "ИИ временно недоступен, но ты всё равно молодец."
        
# --- 4. ЗАПИСЬ И TELEGRAM ---
try:
    if not GOOGLE_CREDS_JSON:
        raise ValueError("GOOGLE_CREDS_JSON is None! Проверь секреты.")

    # Используем переменную из блока CONFIG
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    credentials = Credentials.from_service_account_info(
        creds_dict, 
        scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    )
    ss = gspread.authorize(credentials).open("Garmin_Data")
    
    # 1. Лист Morning (используем нашу функцию обновления)
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    
    # 2. Лист Activities (колонки A-P из прошлого шага)
    act_sheet = ss.worksheet("Activities")
    existing_ids = {r[15] for r in act_sheet.get_all_values() if len(r) > 15}
    for act in activities_to_log:
        if act["id"] not in existing_ids:
            act_sheet.append_row(act["row"], value_input_option='USER_ENTERED')
    
    # 3. AI Log & Telegram
    clean_ai = ai_advice.replace('*', '')
    ss.worksheet("AI_Log").append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), "Info", clean_ai])
    
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        msg = (f"📊 *Garmin Sync Complete*\n\n"
               f"💓 *HRV:* {hrv}\n"
               f"🧬 *Fit Age:* {fit_age}\n"
               f"🌙 *Сон:* {slp_h}ч\n\n"
               f"🤖 {clean_ai}")
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", 
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
    print("🚀 Победа! Всё в таблице и в ТГ.")
except Exception as e:
    print(f"Final Error: {e}")
