import base64
import tarfile
import os
import json
import requests
import garth
import time
import random
from datetime import datetime, timedelta
from garminconnect import Garmin
import gspread
from google.oauth2.service_account import Credentials

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def human_delay():
    """Делает паузу как живой человек, чтобы Garmin не банил."""
    wait_time = random.uniform(3.5, 7.5)
    print(f"⏳ Пауза {round(wait_time, 1)} сек...")
    time.sleep(wait_time)

def safe_call(func, *args, **kwargs):
    """Выполняет запрос к Garmin и, если видит ошибку 429, ждет и пробует еще раз."""
    for attempt in range(2): # 2 попытки вполне хватит
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if "429" in str(e):
                wait = (attempt + 1) * 30
                print(f"⚠️ Garmin перегружен (429). Ждем {wait} сек...")
                time.sleep(wait)
            else:
                raise e # Если ошибка не 429, то пробрасываем её дальше
    return None

# --- CONFIG ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL")
GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS")
GARMIN_SESSION_BASE64 = os.environ.get("GARMIN_SESSION_BASE64")

now = datetime.now()
today_str = now.strftime("%Y-%m-%d")
display_date = now.strftime("%d.%m.%Y")

# --- ЛОГИН GARMIN (КОРРЕКТНЫЙ) ---
session_dir = os.path.abspath("./.garth")
os.makedirs(session_dir, exist_ok=True)

# 1. Пробуем восстановить из секрета, если он есть
if GARMIN_SESSION_BASE64:
    try:
        with open("session.tar.gz", "wb") as f:
            f.write(base64.b64decode(GARMIN_SESSION_BASE64))
        with tarfile.open("session.tar.gz", "r:gz") as tar:
            tar.extractall(path=".")
        print(f"✅ Сессия извлечена")
    except Exception as e:
        print(f"⚠️ Секрет не подошел, идем дальше")

gar = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)

try:
    token_file = os.path.join(session_dir, "oauth1_token.json")
    
    if os.path.exists(token_file):
        print("📂 Загрузка из файла...")
        garth.client.load(session_dir)
        gar.garth = garth.client
        print(f"🚀 Вход выполнен (сессия)")
    else:
        print("🔑 Файлов нет. Вход по паролю...")
        human_delay()
        safe_call(gar.login) 
        # ПРАВИЛЬНЫЙ ВЫЗОВ СОХРАНЕНИЯ:
        garth.save(session_dir) 
        print(f"🚀 Вход выполнен (пароль). Сессия сохранена в {session_dir}")

except Exception as e:
    print(f"🚨 Ошибка: {e}")
    raise e
    
# --- ФУНКЦИЯ ДЛЯ ТАБЛИЦ (БРОНЕБОЙНАЯ ВЕРСИЯ) ---
def update_or_append(sheet, date_str, row_data):
    """Обновляет строку, если дата уже есть, или добавляет новую."""
    try:
        # Пытаемся просто найти ячейку
        cell = sheet.find(date_str)
        if cell:
            # Если нашли, обновляем
            sheet.update(range_name=f"A{cell.row}:Z{cell.row}", values=[row_data], value_input_option="USER_ENTERED")
            print(f"🔄 Данные за {date_str} обновлены без апострофов.")
            return
    except Exception:
        # Если не нашли (любая ошибка), просто идем дальше к добавлению
        pass
    
    # Добавляем новую строку, если поиск не удался
    sheet.append_row(row_data, value_input_option="USER_ENTERED")
    print(f"➕ Добавлена новая запись за {date_str} (формат даты сохранен).")
        
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
human_delay() # Пауза перед первым запросом
summary = safe_call(gar.get_user_summary, today_str) or {}

human_delay() # Пауза между summary и hrv (как ты и просил)
hrv_res = safe_call(gar.get_hrv_data, today_str) or {}

hrv = hrv_res.get("hrvSummary", {}).get("lastNightAvg") or ""
r_hr = summary.get("restingHeartRate") or ""

# --- 2. ВЕС, ЖИР, МЫШЦЫ (На основе твоего лога S2) ---
weight, fat, muscle = "", "", ""
try:
    human_delay() # Пауза перед запросом веса (как в твоем оригинале)
    w_data = safe_call(gar.get_body_composition, (now - timedelta(days=3)).strftime("%Y-%m-%d"), today_str) or {}
    
    weights = w_data.get('dateWeightList', [])
    if weights:
        # Твоя логика: берем самый свежий замер
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

# --- 3. СОН И ВРЕМЯ (Твой рабочий утренний алгоритм + Защита) ---
yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
morning_ts = f"{today_str} 08:00"
slp_sc, slp_h = "", ""

for d in [today_str, yesterday_str]:
    try:
        # 1. Используем нашу новую паузу вместо обычного sleep
        human_delay() 
        
        # 2. Оборачиваем запрос в safe_call
        sleep_data = safe_call(gar.get_sleep_data, d) or {}
        
        dto = sleep_data.get("dailySleepDTO") or {}
        if dto and dto.get("sleepTimeSeconds", 0) > 0:
            slp_h = round(float(dto.get("sleepTimeSeconds")) / 3600, 1)
            
            # Тот самый поиск Score, который у тебя работал
            scores = dto.get("sleepScores") or {}
            slp_sc = scores.get("overall", {}).get("value") or dto.get("sleepScore") or ""
            
            raw_ts = dto.get("sleepEndTimestampLocal")
            if raw_ts:
                # Твоя проверка на тип данных (число или строка ISO)
                if isinstance(raw_ts, (int, float)):
                    morning_ts = datetime.fromtimestamp(raw_ts / 1000).strftime("%Y-%m-%d %H:%M")
                else:
                    morning_ts = str(raw_ts).replace("T", " ")[:16]
            break # Нашли данные — выходим из цикла
    except Exception as e:
        print(f"⚠️ Ошибка в цикле сна для {d}: {e}")
        continue

# --- 4. FITNESS AGE (Логика на основе биомаркеров Garmin) ---
fit_age = ""
try:
    actual_age = 63
    # 1. Влияние пульса покоя (RHR)
    # Garmin хвалит за 45-46. Если RHR <= 48, это отличный бонус.
    rhr_val = int(r_hr) if r_hr else 60
    rhr_impact = (rhr_val - 55) * 0.4  # Чем ниже 55, тем моложе
    
    # 2. Влияние жира (Body Fat)
    # У тебя 18.3%, что для 63 лет — атлетический уровень.
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
    fit_age = "63"
        
# --- 5. ФОРМИРОВАНИЕ СТРОК (Версия на базе рабочего кода 15/03) ---

# 1. Значения для Morning (Максимальный заряд за утро)
morning_bb_max = summary.get("bodyBatteryHighestValue") or summary.get("bodyBatteryMostRecentValue", "")
real_age = 63 

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
# --- 2. ACTIVITIES (Защищенный сбор) ---
activities_to_log = []
try:
    # 1. Используем новую умную паузу вместо обычного time.sleep
    human_delay() 
    
    # 2. Оборачиваем запрос в safe_call и запрашиваем 3 тренировки (этого достаточно для одного дня)
    latest_activities = safe_call(gar.get_activities, 0, 3) or []
    
    for a in latest_activities:
        start_local = a.get("startTimeLocal", "")
        # Твоя проверка: берем только сегодняшние тренировки
        if not start_local.startswith(today_str): continue
        
        act_id = str(a.get("activityId"))
        
        # Мощность: NP или взвешенная средняя
        np_val = a.get('normPower') or a.get('weightedAveragePower', "")
        if_val = a.get('intensityFactor')
        tss_val = a.get('trainingStressScore')
        avg_pwr = a.get('avgPower', "")
        
        # Твой расчет VI (Variability Index)
        vi_val = ""
        try:
            if np_val and avg_pwr and float(avg_pwr) > 0:
                vi_val = round(float(np_val) / float(avg_pwr), 2)
        except:
            vi_val = ""

        # Твое формирование строки для таблицы (индексы сохранены!)
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
            # Твой поиск каденса (велосипедный или общий)
            a.get('averageBikingCadenceInRevPerMinute') or a.get('averageBikingCadence') or "",
            round(float(np_val), 1) if np_val else "", 
            round(float(tss_val), 1) if tss_val else "", 
            vi_val, 
            f"'{act_id}" # Твой апостроф для Google Sheets
        ]
        activities_to_log.append({"id": act_id, "row": row_data})

    # Твой reverse для правильного порядка записи
    activities_to_log.reverse() 

except Exception as e:
    print(f"Activity Error: {e}")

# --- ANALYTICS: CTL / ATL / TSB + READINESS ---

ctl = atl = tsb = ""
ftp_est = ""
readiness_score = 0
readiness_text = ""
rd_icon = "🟡"  # <-- Добавь эту строку здесь (базовое значение)
existing_ids = set()

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

    for r in rows[-60:]:
        try:
            if len(r) < 14:
                continue 
            
            sport = str(r[1]).lower() if len(r) > 1 else ""

            raw_tss = str(r[13]).replace(',', '.').strip() if r[13] else "0"
            tss = float(raw_tss) if raw_tss and raw_tss != "None" else 0
            
            raw_pwr = str(r[10]).replace(',', '.').strip() if r[10] else "0"
            avg_power = float(raw_pwr) if raw_pwr and raw_pwr != "None" else 0
            
            raw_dur = str(r[2]).replace(',', '.').strip() if r[2] else "0"
            duration_h = float(raw_dur)

            tss_list.append(tss)

            if "cycling" in sport and duration_h >= 0.3 and avg_power > 0:
                power_candidates.append(avg_power)

        except Exception as e:
            print(f"Строка пропущена: {e}")
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

    if power_candidates:
        ftp_est = round(max(power_candidates) * 0.95, 0)
    else:
        ftp_est = "N/A"

except Exception as e:
    print(f"🚨 Ошибка в блоке аналитики: {e}")
    # На случай ошибки задаем значения по умолчанию, чтобы Readiness не упал
    ctl = atl = tsb = 0
    ftp_est = "N/A"    
        
# --- 2. РАСЧЕТ ГОТОВНОСТИ (СБАЛАНСИРОВАННАЯ ВЕРСИЯ) ---
    # Базовая готовность 2.5 (нейтрально), дальше плюсуем или минусуем
    readiness_score = 2.5
    
    # 1. HRV (Вес: 1.0)
    try:
        hrv_val = int(float(morning_row[5])) if morning_row[5] not in ["", None] else 0
        if hrv_val > 75: readiness_score += 1.0
        elif hrv_val > 65: readiness_score += 0.5
        elif hrv_val < 45: readiness_score -= 1.0
    except: pass

    # 2. Пульс покоя (RHR) (Вес: 0.5)
    try:
        rhr_val = int(float(morning_row[4])) if morning_row[4] not in ["", None] else 0
        if 0 < rhr_val < 50: readiness_score += 0.5
        elif rhr_val > 60: readiness_score -= 0.5
    except: pass

    # 3. Сон (Вес: 1.5) - делаем мягче
    try:
        sleep_hrs = float(morning_row[8]) if morning_row[8] not in ["", None] else 0
        if sleep_hrs >= 7.5: readiness_score += 1.0
        elif sleep_hrs < 6.0: readiness_score -= 1.0 # Раньше было -2, это слишком
    except: pass

    # 4. Body Battery (Вес: 0.5)
    try:
        bb_val = int(float(morning_row[6])) if morning_row[6] not in ["", None] else 0
        if bb_val > 80: readiness_score += 0.5
        elif bb_val < 40: readiness_score -= 0.5
    except: pass

    # 5. Ограничитель по перегрузке (TSB)
    try:
        t_val = float(tsb) if tsb and str(tsb).strip() not in ["", "N/A", "None"] else 0
        if t_val < -25: readiness_score -= 1.5
        elif t_val < -15: readiness_score -= 0.5
    except: pass

    # Ограничиваем рамками 0 и 5
    readiness_score = max(0, min(5, round(readiness_score, 1)))

    # --- Интерпретация текста ---
    if readiness_score >= 4:
        readiness_text = "🔥 Отличная готовность — идеальный день для рекордов"
        rd_icon = "🔥🏆"
    elif readiness_score >= 3:
        readiness_text = "👍 Хорошая готовность — можно тренироваться уверенно"
        rd_icon = "🟢🟢"
    elif readiness_score >= 2:
        readiness_text = "⚠️ Средняя готовность — работаем, но без фанатизма"
        rd_icon = "🟢🟡"
    elif readiness_score >= 1:
        readiness_text = "🟠 Низкая готовность — лучше восстановиться"
        rd_icon = "🟠"
    else:
        readiness_text = "🚨 Критическая усталость — строгий отдых"
        rd_icon = "🔴"

# --- 3. AI BLOCK ---
ai_advice = ""
report_type = ""
ftp_status = ""
display_date = now.strftime("%d.%m.%Y") # Вынес сюда для надежности
# Теперь используем уже открытый ss (из начала скрипта)
log_sheet = ss.worksheet("AI_Log")
last_logs = log_sheet.get_all_values()

# Проверка утреннего отчета
morning_done_today = any(len(row) > 1 and str(row[0]).startswith(today_str) and "Morning" in str(row[1]) for row in last_logs)

if activities_to_log:
    report_type = "Activity"
    act = activities_to_log[-1]['row']

    # Проверка адекватности FTP (по совету ChatGPT)
    ftp_status = ""
    # Извлекаем NP из данных текущей тренировки
    current_act_np = act[12] # Это надежнее 
    if current_act_np and ftp_est and ftp_est != "N/A":
        try:
            if float(current_act_np) > float(ftp_est):
                ftp_status = f"⚠️ Внимание: Твоя мощность (<code>{current_act_np}W</code>) выше расчетного FTP! Ты сильнее, чем думает система!\n"
        except:
            pass
            
    # Добавляем контекст формы и готовности в промпт
    prompt = (
        f"Ты — опытный спортивный коуч по имени АРНИ. Проведи конструктивный разбор сессии."
        f"\nСТРУКТУРА ОТЧЕТА:"
        f"\n1. Заголовок: <b>Анализ тренировки от {display_date}</b>"
        f"\n2. Используй жирные заголовки разделов."
        f"\n3. В конце подпись: С уважением, <b>АРНИ</b>."
        f"\n\nДанные сессии: Тип: {act[1]}, Дистанция: {act[3]}км, Мощность: {act[10]}Вт (NP: {act[12]}Вт), "
        f"TSS: {act[13]}, IF: {act[6]}. "
        f"\nКонтекст атлета: Баланс нагрузки (TSB): {tsb}, CTL: {ctl}, ATL: {atl}, "
        f"Готовность (Readiness Score): {readiness_score}/5, Состояние: {readiness_text}. "
        f"\nИНСТРУКЦИИ:"
        f"\n1. Используй цифры NP (нормализованная мощность) и TSS как главный факт нагрузки."
        f"\n2. Если тип 'Walking' или данные мощности отсутствуют, не критикуй — оценивай это как восстановление или активность по шагам."
        f"\n3. Учти соотношение ATL/CTL: если нагрузка (ATL) в 2 раза выше базы (CTL), предупреди о риске травм."
        f"\n4. Если TSB сильно отрицательный (ниже -20), похвали за дисциплину, но жестко настаивай на отдыхе."
        f"\n5. Твой стиль: профессиональный, мотивирующий, но честный."
        f"\n6. В конце дай краткий совет на завтра. Без грубости."
        )

elif not morning_done_today:
    report_type = "Morning"
    
    # Умный промпт для ИИ-аналитика
    prompt = (
        f"Ты — элитный спортивный директор по имени АРНИ. Тон: профессиональный, мотивирующий. "
        f"\nСТРУКТУРА ОТЧЕТА (ОБЯЗАТЕЛЬНО):"
        f"\n1. Заголовок: <b>Утренний Отчет о Состоянии Атлета</b>"
        f"\n2. Дата: <b>{display_date}</b>"
        f"\n3. Тема: Оценка утренней готовности к тренировочному дню."
        f"\n4. Используй жирные заголовки разделов (<b>1. Сердечно-сосудистая система:</b> и т.д.)."
        f"\n5. В конце подпись: С уважением, <b>АРНИ</b> (Твой Элитный Спортивный Директор)."
        f"\n\nДанные атлета: HRV {morning_row[5]}, Пульс {morning_row[4]}, Сон {morning_row[8]}ч, "
        f"BB {morning_row[6]}, Fit Age {morning_row[10]}. "
        f"Форма: CTL {ctl}, ATL {atl}, TSB {tsb}. "
        f"Готовность: {readiness_score}/5. {ftp_status} "
        f"\nИНСТРУКЦИИ:"
        f"\n1. Это УТРЕННИЙ отчет о состоянии покоя. Не ищи данные тренировок."
        f"\n2. Атлету 63 года. Его HRV > 70 и RHR < 50 — это ПОКАЗАТЕЛИ ЭЛИТНОЙ ФОРМЫ для этого возраста. Обязательно хвали за это!"
        f"\n3. Атлет весит 87 кг, но это МЫШЦЫ (жир ~18%). Категорически не предлагай худеть."
        f"\n4. TSB до -25 — это нормальный процесс загрузки."
        f"\n5. Fit Age {morning_row[10]} при реальных 63 годах — отличный результат. Интерпретируй колебания только как временный стресс."
        f"\n6. Оцени: нужно ли сегодня восстанавливаться или можно грузиться."
        )
else:
    ai_advice = "SKIP"
    print("🚀 Отчеты уже отправлены, новых активностей нет.")

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
            
            # ПРОВЕРКА СТАТУСА ОТВЕТА
            if res_ai.status_code != 200:
                ai_advice = f"⚠️ Ошибка API Gemini (Код: {res_ai.status_code}). Анализ временно недоступен."
                # Выходим из цикла попыток, так как есть ответ от сервера (хоть и с ошибкой)
            
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
        clean_ai = ai_advice.replace('**', '').replace('*', '').strip()
        log_sheet.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), report_type, clean_ai])
        
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            # 1. Заголовок и основные цифры
            if report_type == "Activity":
                act = activities_to_log[-1]['row']
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
                
                if act[12] and str(act[12]).strip() not in ["0", "", "None"]:
                    parts.append(f"⚡ NP {act[12]}W")
                
                if tss_val > 0:
                    parts.append(f"{tss_icon} TSS {int(tss_val)}")
                
                stats = f"📊 <code>{' | '.join(parts)}</code>"

            else: # <--- ИСПРАВЛЕНО (добавлено двоеточие)
                header = "<b>🌞 ДОБРОЕ УТРО, КАПИТАН!</b>"
                stats = f"<code>📈 HRV: {morning_row[5]} | 💓 RHR: {morning_row[4]} | 🔋 BB: {morning_row[6]}</code>"

            # 2. Универсальный блок аналитики
            fit_age_info = f" | 🧬 Fit Age: <code>{morning_row[10]}</code>" if report_type == "Morning" else ""
            
            
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
