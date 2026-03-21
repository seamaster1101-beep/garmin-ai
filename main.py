import os
import json
import requests
import garth
import time
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
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
# now = datetime.now() # Временно комментируем текущее время
##now = datetime.now() - timedelta(days=1) # Устанавливаем "вчера"
##today_str = now.strftime("%Y-%m-%d")

def update_or_append(sheet, date_str, row_data):
    try:
        col_values = sheet.col_values(1)
        search_date = date_str.split(' ')[0] # Получаем только "ГГГГ-ММ-ДД"
        found_idx = -1
        for i, val in enumerate(col_values):
            # Проверяем, что ячейка в таблице начинается с нашей даты
            if str(val).startswith(search_date):
                found_idx = i + 1
                break
        if found_idx != -1:
            sheet.update(range_name=f"A{found_idx}", values=[row_data], value_input_option='USER_ENTERED')
        else:
            sheet.append_row(row_data, value_input_option='USER_ENTERED')
    except Exception as e: 
        print(f"Err gspread update: {e}")

# --- ЛОГИН GARMIN (ВЕРСИЯ С МАСКИРОВКОЙ) ---
import time
import random

session_dir = "./.garth"
gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)

# ⏳ Маскировка: случайная пауза перед стартом (от 5 до 15 секунд)
print(f"⏳ Маскировка запуска... Ожидание {random.randint(5, 15)} сек.")
time.sleep(random.randint(5, 15))

try:
    if os.path.exists(session_dir) and os.listdir(session_dir):
        print("✅ Файлы сессии найдены. Проверяем...")
        garth.resume(session_dir)
        gar.garth = garth.client
        
        if not gar.display_name:
            print("⚠️ Сессия пустая. Ждем 30 сек перед входом по паролю...")
            time.sleep(30) # Даем серверу остыть
            gar.login(session_dir)
        else:
            print(f"🚀 Сессия подтверждена: {gar.display_name}")
    else:
        print("🔑 Сессия не найдена. Первый вход...")
        gar.login(session_dir)

except Exception as e:
    if "429" in str(e):
        print("⚠️ Обнаружен лимит 429. Ждем 2 минуты перед ПОВТОРНОЙ попыткой...")
        time.sleep(120) # Ключевая пауза при блокировке
    else:
        print(f"⚠️ Ошибка сессии ({e}). Пауза 10 сек...")
        time.sleep(10)
    
    try:
        gar.login(session_dir)
    except Exception as e2:
        print(f"🚨 КРИТИЧЕСКАЯ ОШИБКА: {e2}")
        raise e2

if not gar.display_name:
    raise ValueError("Display Name is None - Stopping script.")

# --- ИНИЦИАЛИЗАЦИЯ GOOGLE ---
try:
    if not GOOGLE_CREDS_JSON:
        raise ValueError("Секрет GOOGLE_CREDS пуст!")
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    credentials = Credentials.from_service_account_info(creds_dict, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    ss = gspread.authorize(credentials).open("Garmin_Data")
    print("✅ Таблица Google подключена!")
except Exception as e:
    print(f"🚨 Ошибка Google: {e}")
    raise e

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

    # Разворачиваем список собранных тренировок перед записью в таблицу
    activities_to_log.reverse() 

except Exception as e:
    print(f"Activity Error: {e}")

# --- ANALYTICS: CTL / ATL / TSB + READINESS ---

ctl = atl = tsb = ""
ftp_est = ""
readiness_score = 0
readiness_text = ""
existing_ids = set() # Создаем заранее

try:
    act_sheet = ss.worksheet("Activities")
    # ОДИН ЗАПРОС К GOOGLE: берем всё сразу
    all_rows = act_sheet.get_all_values()
    
    # 1. Сразу вытаскиваем существующие ID для финала скрипта
    existing_ids = {r[15] for r in all_rows if len(r) > 15}
    
# 2. Берем данные для анализа (без шапки)
    rows = all_rows[1:] 

    tss_list = []
    power_candidates = []

    # ВАЖНО: Весь этот блок ниже должен быть сдвинут вправо!
    for r in rows[-60:]:
        try:
            # 1. Базовая проверка длины
            if len(r) < 14: continue 
            
            # 2. Определяем спорт
            sport = str(r[1]).lower() if len(r) > 1 else ""

            # 3. Чистим и берем TSS (колонка 14)
            raw_tss = str(r[13]).replace(',', '.').strip() if r[13] else "0"
            tss = float(raw_tss) if raw_tss and raw_tss != "None" else 0
            
            # 4. Мощность берем только если она есть
            raw_pwr = str(r[10]).replace(',', '.').strip() if r[10] else "0"
            avg_power = float(raw_pwr) if raw_pwr and raw_pwr != "None" else 0
            
            raw_dur = str(r[2]).replace(',', '.').strip() if r[2] else "0"
            duration_h = float(raw_dur)

            # Добавляем TSS в любом случае
            tss_list.append(tss)

            # Кандидаты на FTP — только если это вело и есть мощность
            if "cycling" in sport and duration_h >= 0.3 and avg_power > 0:
                power_candidates.append(avg_power)

        except Exception as e:
            print(f"Строка пропущена из-за ошибки: {e}")
            continue
            
    def ewma(data, alpha):
        if not data:
            return 0
        result = data[0]
        for x in data[1:]:
            result = alpha * x + (1 - alpha) * result
        return result

    if tss_list:
        ctl = round(ewma(tss_list, 2/(42+1)), 1)
        atl = round(ewma(tss_list, 2/(7+1)), 1)
        tsb = round(ctl - atl, 1)

    else:
        ctl = atl = tsb = 0

    # --- FTP estimate ---
    if power_candidates:
        best_power = max(power_candidates)
        ftp_est = round(best_power * 0.95, 0)
    else:
        ftp_est = "N/A" # Или оставь пустую строку ""
except Exception as e:
    print(f"Analytics (CTL/ATL) Error: {e}")

# --- READINESS SCORE ---
try:
    # 1. HRV
    if hrv:
        hrv_val = int(float(hrv))
        if hrv_val > 50: readiness_score += 2
        elif hrv_val < 40: readiness_score -= 2

    # 2. Пульс покоя
    if r_hr:
        rhr_val = int(float(r_hr))
        if rhr_val < 55: readiness_score += 1
        elif rhr_val > 60: readiness_score -= 1

    # 3. Сон
    if slp_h:
        slp_val = float(slp_h)
        if slp_val >= 7: readiness_score += 1
        elif slp_val < 6: readiness_score -= 1

    # 4. Body Battery
    if morning_bb_max:
        bb_val = int(float(morning_bb_max))
        if bb_val > 70: readiness_score += 1
        elif bb_val < 40: readiness_score -= 1

    # 5. Нагрузка (TSB) — Спортивная логика (по совету ChatGPT)
    if tsb != "" and tsb is not None:
        tsb_val = float(tsb)
        if tsb_val < -25: 
            readiness_score -= 2  # Зона риска, нужен отдых
        elif tsb_val < -10: 
            readiness_score -= 1  # Рабочая нагрузка, все ок
        elif tsb_val > 10: 
            readiness_score += 1  # Состояние "свежести"

        # --- Интерпретация готовности (Исправлено) ---
        # --- ОБНОВЛЕННАЯ ЛОГИКА СТАТУСОВ ГОТОВНОСТИ ---
        if readiness_score >= 4:
            readiness_text = "🔥 Отличная готовность — идеальный день для рекордов"
        elif readiness_score == 3:
            readiness_text = "👍 Хорошая готовность — можно тренироваться в полную силу"
        elif readiness_score >= 1:
            readiness_text = "⚠️ Средняя готовность — рекомендуется умеренная нагрузка"
        elif readiness_score >= -1:
            readiness_text = "📉 Низкая готовность — лучше ограничиться легкой активностью"
        else:
            readiness_text = "🚨 Критический уровень стресса — необходим полный отдых"

except Exception as e:
    print(f"Readiness Calculation Error: {e}")

# --- 3. AI BLOCK ---
ai_advice = ""
report_type = ""
ftp_status = ""
# Теперь используем уже открытый ss (из начала скрипта)
log_sheet = ss.worksheet("AI_Log")
last_logs = log_sheet.get_all_values()

# Проверка утреннего отчета
morning_done_today = any(today_str in row[0] and "Morning" in row[1] for row in last_logs)

if activities_to_log:
    report_type = "Activity"
    act = activities_to_log[0]['row']

    # Проверка адекватности FTP (по совету ChatGPT)
    ftp_status = ""
    # Извлекаем NP из данных текущей тренировки
    current_act_np = activities_to_log[0]['row'][12] 
    if current_act_np and ftp_est and ftp_est != "N/A":
        if float(current_act_np) > float(ftp_est):
            ftp_status = "⚠️ Внимание: Твоя мощность выше расчетного FTP! Ты сильнее, чем думает система!\n"
            
    # Добавляем контекст формы и готовности в промпт
    prompt = (f"Ты — опытный спортивный коуч. Проведи конструктивный разбор сессии: "
              f"Тип: {act[1]}, Дистанция: {act[3]}км, Мощность: {act[10]}Вт (NP: {act[12]}Вт), "
              f"TSS: {act[13]}, IF: {act[6]}. "
              f"\nКонтекст атлета: Баланс нагрузки (TSB): {tsb}, "
              f"Готовность (Readiness Score): {readiness_score}/5, Состояние: {readiness_text}. "
              f"\nВАЖНО: Используй цифры NP и TSS как факт нагрузки. "
              f"Учти текущий TSB: если он сильно отрицательный, похвали за работу, но предупреди об отдыхе. "
              f"Твой стиль: профессиональный, мотивирующий, но честный. "
              f"В конце дай краткий совет на завтра. Без грубости.")

elif not morning_done_today:
    report_type = "Morning"
    # Умный промпт для ИИ-аналитика
    prompt = (
        f"Ты — элитный спортивный директор. Тон: профессиональный, без паники. "
        f"Данные атлета: HRV {morning_row[5]}, Пульс {morning_row[4]}, Сон {morning_row[8]}ч, "
        f"BB {morning_row[6]}, Fit Age {morning_row[10]}. "
        f"Форма: CTL {ctl}, ATL {atl}, TSB {tsb}. "
        f"Готовность: {readiness_score}/5. {ftp_status} "
        f"\nИНСТРУКЦИЯ: "
        f"1. HRV > 100 и RHR < 50 — это ПРИЗНАК СИЛЫ, не называй это ошибкой. "
        f"2. Атлет весит 87 кг, но это МЫШЦЫ (жир ~12%). Не предлагай худеть. "
        f"3. TSB до -25 — это нормальный тренировочный процесс. "
        f"4. Оцени: нужно ли сегодня восстанавливаться или можно грузиться."
    )
else:
    ai_advice = "SKIP"

# ... (дальше твой код с запросом к Gemini и отправкой в Telegram остается без изменений)

# Запрос к Gemini (Универсальный метод)
if GEMINI_API_KEY and ai_advice != "SKIP":
    try:
        # 1. Получаем список моделей
        res_m = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}", 
            timeout=15
        )
        models_data = res_m.json()
        available = [
            m["name"] for m in models_data.get("models", []) 
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
        
        if not available:
            ai_advice = "⚠️ Ошибка: Доступные модели Gemini не найдены."
        else:
            # 2. Выбираем модель
            target_model = next((m for m in available if "flash" in m), available[0])
            
            # 3. Запрос контента
            url = f"https://generativelanguage.googleapis.com/v1beta/{target_model}:generateContent?key={GEMINI_API_KEY}"
            res_ai = requests.post(
                url, 
                json={"contents": [{"parts": [{"text": prompt}]}]}, 
                timeout=30
            )
            
            data = res_ai.json()
            
            # --- ПРОВЕРКА ОТВЕТА (только один раз и внутри else!) ---
            if "candidates" in data and data["candidates"]:
                ai_advice = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            else:
                feedback = data.get('promptFeedback', {})
                error_msg = data.get('error', {}).get('message')
                reason = feedback.get('blockReason') or error_msg or "Ответ пуст (возможно, цензура)"
                ai_advice = f"🤖 ИИ не дал совета. Причина: {reason}"
            
    except Exception as e:
        ai_advice = f"🚨 Ошибка блока ИИ: {str(e)}"

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
    for act in activities_to_log:
        if act["id"] not in existing_ids:
            act_sheet.append_row(act["row"], value_input_option='USER_ENTERED')
            new_row_idx = len(act_sheet.get_all_values())
            act_sheet.format(f"A{new_row_idx}", {"horizontalAlignment": "LEFT"})
    
    # --- 3. ОТПРАВКА В TELEGRAM И ЛОГ ---
    if ai_advice and ai_advice != "SKIP":
        clean_ai = ai_advice.replace('*', '').replace('<', '&lt;').replace('>', '&gt;').strip()
        log_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), report_type, clean_ai])
        
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            # 1. Заголовок и основные цифры
            if report_type == "Activity":
                act = activities_to_log[0]['row']
                act_type = str(act[1]).lower()
                icon = "🚴‍♂️" if "cycling" in act_type else "🏋️‍♂️" if "strength" in act_type else "🏃‍♂️"
                header = f"<b>{icon} НОВАЯ ТРЕНИРОВКА</b>"
                
                # --- ОБНОВЛЕННЫЙ БЛОК ТРЕНИРОВКИ ---
                try:
                    tss_val = float(act[13]) if act[13] else 0
                except: 
                    tss_val = 0
                
                tss_icon = "👑" if tss_val >= 100 else "🔥" if tss_val >= 70 else "📈"
                
                parts = [f"{act[3]}км"]
                
                # Проверка мощности (NP)
                if act[12] and str(act[12]).strip() not in ["0", "", "None"]:
                    parts.append(f"⚡ NP {act[12]}W")
                
                # Проверка TSS
                if tss_val > 0:
                    parts.append(f"{tss_icon} TSS {int(tss_val)}")
                
                stats = f"📊 <code>{' | '.join(parts)}</code>"
                # ----------------------------------

            else:
                header = "<b>🌞 ДОБРОЕ УТРО, КАПИТАН!</b>"
                stats = f"<code>📈 HRV: {morning_row[5]} | 💓 RHR: {morning_row[4]} | 🔋 BB: {morning_row[6]}</code>"

            # 2. Универсальный блок аналитики
            fit_age_info = f" | 🧬 Fit Age: <code>{morning_row[10]}</code>" if report_type == "Morning" else ""
            rd_icon = ("🟢" * readiness_score) if readiness_score > 0 else ("🔴" * abs(readiness_score)) if readiness_score < 0 else "🟡"
            
            analytics_block = (
                f"\n\n📊 <b>Аналитика формы:</b>\n"
                f"<code>CTL: {ctl} | ATL: {atl} | TSB: {tsb}</code>{fit_age_info}\n"
                f"{ftp_status if 'ftp_status' in locals() else ''}"
                f"🔋 <b>Readiness:</b> <code>{readiness_score}/5</code> {rd_icon}\n"
                f"💬 <i>{readiness_text}</i>"
            )

            if ftp_est and ftp_est != "N/A" and report_type == "Morning":
                analytics_block += f"\n🚴 <b>Est. FTP:</b> <code>{ftp_est} W</code>"

            # 3. Сборка и обрезка
            intro = f"{header}\n{stats}{analytics_block}\n\n"
            
            if len(intro + clean_ai) > 4000:
                allowed_len = 4000 - len(intro)
                clean_ai = clean_ai[:allowed_len] + "...\n\n<i>(текст обрезан)</i>"
            
            msg = f"{intro}{clean_ai}"
            
            # 4. Отправка
            tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}
            
            try:
                tg_res = requests.post(tg_url, json=payload, timeout=15)
                if tg_res.status_code != 200:
                    requests.post(tg_url, json={"chat_id": TELEGRAM_CHAT_ID, "text": f"⚠️ Ошибка HTML. Текст:\n\n{msg[:3900]}"}, timeout=15)
            except Exception as e:
                print(f"🚨 Ошибка сети TG: {e}")

except Exception as e:
    print(f"🚨 Ошибка выполнения финальной стадии: {e}")
