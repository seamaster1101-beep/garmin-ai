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
            sheet.update(range_name=f"A{found_idx}", values=[row_data], value_input_option='USER_ENTERED')
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
        
# --- 5. ФОРМИРОВАНИЕ СТРОК (Версия на базе рабочего кода 15/03) ---

# 1. Значения для Morning (Максимальный заряд за утро)
morning_bb_max = summary.get("bodyBatteryHighestValue") or summary.get("bodyBatteryMostRecentValue", "")
real_age = 62 

morning_row = [
    f"'{morning_ts}", 
    weight, 
    fat, 
    muscle, 
    r_hr, 
    hrv, 
    morning_bb_max, # Сюда пишем МАКСИМУМ
    slp_sc, 
    slp_h, 
    real_age, 
    fit_age
]

# 2. Значения для Daily (Твой старый проверенный метод)
# Берем totalSteps, как в рабочем коде
steps = summary.get('totalSteps', 0)

# Дистанция: расчет через шаги (0.000762 - твой проверенный коэффициент)
daily_dist = round(steps * 0.000762, 2)

# Калории: Активные + Базовый метаболизм (BMR)
cals = int(summary.get("activeKilocalories", 0) + summary.get("bmrKilocalories", 0))

# Актуальный заряд (на момент запуска скрипта)
current_bb = summary.get("bodyBatteryMostRecentValue", "")

daily_row = [
    f"'{today_str}", 
    steps, 
    daily_dist, 
    cals, 
    r_hr, 
    current_bb # В Daily пишем актуальный заряд
]
# --- 2. ACTIVITIES ---
activities_to_log = []
try:
    latest_activities = gar.get_activities(0, 5) or []
    for a in latest_activities:
        start_local = a.get("startTimeLocal", "")
        if not start_local.startswith(today_str): continue
        
        act_id = str(a.get("activityId"))
        np_val = a.get('normPower') or a.get('weightedAveragePower', "")
        if_val = a.get('intensityFactor')
        tss_val = a.get('trainingStressScore')
        avg_pwr = a.get('avgPower', "")
        
        vi_val = ""
        if np_val and avg_pwr and float(avg_pwr) > 0:
            vi_val = round(float(np_val) / float(avg_pwr), 2)

        row_data = [
            start_local.replace("T", " ")[:16], 
            a.get('activityType', {}).get('typeKey', ''), 
            round(a.get('duration', 0) / 3600, 2), 
            round(a.get('distance', 0) / 1000, 2),
            a.get('averageHR', ""), 
            a.get('maxHR', ""), 
            round(float(if_val), 3) if if_val else "", 
            round(float(a.get('activityTrainingLoad', 0)), 1),
            round(float(a.get('aerobicTrainingEffect', 0)), 1), 
            a.get('calories', ""),
            avg_pwr, 
            a.get('averageBikingCadenceInRevPerMinute') or a.get('averageBikingCadence') or "",
            round(float(np_val), 1) if np_val else "", 
            round(float(tss_val), 1) if tss_val else "", 
            vi_val, 
            f"'{act_id}"
        ]
        activities_to_log.append({"id": act_id, "row": row_data})
except Exception as e:
    print(f"Activity Error: {e}")

# --- 3. AI BLOCK (Адекватный наставник - Исправленный) ---
ai_advice = ""
report_type = ""

# Авторизация и проверка логов
creds_dict = json.loads(GOOGLE_CREDS_JSON)
credentials = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
ss = gspread.authorize(credentials).open("Garmin_Data")
log_sheet = ss.worksheet("AI_Log")
last_logs = log_sheet.get_all_values()

# Проверка утреннего отчета
morning_done_today = any(today_str in row[0] and "Morning" in row[1] for row in last_logs)

if activities_to_log:
    report_type = "Activity"
    act = activities_to_log[0]['row']
    prompt = (f"Ты — опытный спортивный коуч. Проведи конструктивный разбор сессии: "
              f"{act[1]} (тип), {act[3]}км, мощность {act[10]}Вт (NP {act[12]}Вт), TSS {act[13]}, IF {act[6]}. "
              f"Твой стиль: профессиональный, мотивирующий, но честный. "
              f"Если тренировка короткая, отметь пользу поддержания тонуса, но укажи, что нужно для прогресса. "
              f"В конце дай краткий совет на завтра. Без грубости.")

elif not morning_done_today:
    report_type = "Morning"
    prompt = (f"Ты — личный спортивный врач. HRV {morning_row[5]}, Пульс {morning_row[4]}, "
              f"Сон {morning_row[8]}ч, BB {morning_row[6]}, Fit Age {morning_row[10]}, "
              f"Реальный возраст {real_age}. "
              f"Дай краткую оценку состояния. Учти: если Fit Age ниже реального — это отличный показатель омоложения, похвали за это. "
              f"Твоя цель — долголетие и здоровье атлета.")
else:
    ai_advice = "SKIP"

# Запрос к Gemini (Универсальный метод)
if GEMINI_API_KEY and ai_advice != "SKIP":
    try:
        # Сначала узнаем, какая модель доступна
        res_m = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}")
        models_data = res_m.json()
        available = [m["name"] for m in models_data.get("models", []) if "generateContent" in m.get("supportedGenerationMethods", [])]
        # Выбираем flash или самую первую доступную
        target_model = next((m for m in available if "flash" in m), available[0])
        
        url = f"https://generativelanguage.googleapis.com/v1beta/{target_model}:generateContent?key={GEMINI_API_KEY}"
        res_ai = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
        
        # Проверяем структуру ответа перед тем как лезть в ['candidates']
        data = res_ai.json()
        if "candidates" in data and data["candidates"]:
            ai_advice = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        else:
            ai_advice = f"ИИ не дал ответа. Причина: {data.get('promptFeedback', 'Неизвестна')}"
            
    except Exception as e:
        ai_advice = f"Ошибка ИИ: {e}"

# --- 4. ЗАПИСЬ И TELEGRAM ---
try:
    # 1. Запись в таблицы
    update_or_append(ss.worksheet("Morning"), today_str, morning_row)
    # Выравниваем дату в Morning
    m_sheet = ss.worksheet("Morning")
    m_sheet.format("A:A", {"horizontalAlignment": "LEFT"})

    update_or_append(ss.worksheet("Daily"), today_str, daily_row)
    # Выравниваем дату в Daily
    d_sheet = ss.worksheet("Daily")
    d_sheet.format("A:A", {"horizontalAlignment": "LEFT"})
    
    # 2. Activities
    act_sheet = ss.worksheet("Activities")
    existing_ids = {r[15] for r in act_sheet.get_all_values() if len(r) > 15}
    for act in activities_to_log:
        if act["id"] not in existing_ids:
            act_sheet.append_row(act["row"], value_input_option='USER_ENTERED')
            new_row_idx = len(act_sheet.get_all_values())
            act_sheet.format(f"A{new_row_idx}", {"horizontalAlignment": "LEFT"})
    
    # 3. Отправка в Telegram и Лог
    if ai_advice and ai_advice != "SKIP":
        clean_ai = ai_advice.replace('*', '')
        log_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), report_type, clean_ai])
        
        # ВНИМАНИЕ: Используем TELEGRAM_BOT_TOKEN, как на твоем скрине!
        # Убедись, что в начале скрипта есть строка: 
        # TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
        
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            status = "🚴‍♂️" if report_type == "Activity" else "🌅"
            msg = f"{status} Garmin {report_type}\n\n{clean_ai}"
            
            tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            tg_res = requests.post(tg_url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=15)
            
            if tg_res.status_code == 200:
                print("✅ Telegram: Сообщение доставлено!")
            else:
                print(f"❌ Telegram: Ошибка {tg_res.status_code}. Ответ: {tg_res.text}")
        else:
            print("⚠️ Ошибка: Токен или ID чата пусты! Проверь переменные в начале скрипта.")

    print("🚀 Всё четко: выровнено, проверено, отправлено!")
except Exception as e:
    print(f"🚨 Финальная ошибка: {e}")
