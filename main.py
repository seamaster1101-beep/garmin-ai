import os
import requests
import json
from datetime import datetime, timedelta
import sys

# --- CONFIG ---
BIO_AGE = 63  # твой возраст

def get_env(name):
    val = os.environ.get(name)
    if not val:
        print(f"❌ Нет переменной: {name}")
        sys.exit(1)
    return val

SPREADSHEET_ID = "1rxg5oqDXWXwHSHMmR-RbJuad8rXe2OdmCEMUMY2SBT4"

CLIENT_ID = get_env('STRAVA_CLIENT_ID')
CLIENT_SECRET = get_env('STRAVA_CLIENT_SECRET')
REFRESH_TOKEN = get_env('STRAVA_REFRESH_TOKEN')
TELEGRAM_BOT_TOKEN = get_env('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = get_env('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = get_env('GEMINI_API_KEY')
GOOGLE_CREDS_JSON = get_env('GOOGLE_CREDS')

FTP = 250

# --- TELEGRAM ---
def send_tg(msg):
    if len(msg) > 4000:
        msg = msg[:3900]
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=15
        )
    except:
        pass

# --- STRAVA ---
def get_strava_data():
    try:
        res = requests.post("https://www.strava.com/oauth/token", data={
            'client_id': CLIENT_ID,
            'client_secret': CLIENT_SECRET,
            'refresh_token': REFRESH_TOKEN,
            'grant_type': 'refresh_token'
        }, timeout=15)

        token = res.json().get('access_token')
        if not token:
            return []

        r = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 100},
            timeout=15
        )

        data = r.json()
        return data if r.status_code == 200 and isinstance(data, list) else []

    except Exception as e:
        print("Strava error:", e)
        return []

# --- GOOGLE SHEETS ---
def get_morning_metrics(target_date):
    try:
        import gspread
        from google.oauth2.service_account import Credentials

        creds = Credentials.from_service_account_info(
            json.loads(GOOGLE_CREDS_JSON),
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )

        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        records = sheet.get_all_records()

        for row in reversed(records):
            if target_date in str(row.get('Date', '')):
                return row

        yesterday = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        for row in reversed(records):
            if yesterday in str(row.get('Date', '')):
                print("⚠️ Использую вчерашние данные")
                return row

    except Exception as e:
        print("Sheets error:", e)

    return {}

# НОВАЯ ФУНКЦИЯ ЗАПИСИ (Вставляем сюда)
def update_fitness_age(target_date, f_age_val):
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        
        creds = Credentials.from_service_account_info(
            json.loads(GOOGLE_CREDS_JSON),
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        
        client = gspread.authorize(creds)
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        
        # 1. Получаем все значения из первой колонки (Date)
        dates_col = sheet.col_values(1) 
        
        # 2. Ищем строку, в которой содержится наша дата (target_date)
        row_idx = -1
        for i, val in enumerate(dates_col):
            if target_date in val: # Ищем вхождение текста "2026-03-28" в "2026-03-28 03:15"
                row_idx = i + 1
                break
        
        if row_idx != -1:
            header = sheet.row_values(1)
            if "Fitness_Age" in header:
                col_idx = header.index("Fitness_Age") + 1
                sheet.update_cell(row_idx, col_idx, f_age_val)
                print(f"✅ Fitness Age ({f_age_val}) успешно записан в строку {row_idx}")
            else:
                print("❌ Колонки Fitness_Age не существует")
        else:
            print(f"❌ Дата {target_date} не найдена в колонке Date")
            
    except Exception as e:
        print(f"Ошибка записи: {e}")

# --- TSS ---
def calc_tss(a):
    w = a.get("average_watts")
    t = a.get("moving_time", 0)
    if not w:
        return 0
    return round((t/3600)*(w/FTP)**2*100,1)

# --- VO2 ---
def estimate_vo2max(activities):
    vals = []
    for a in activities:
        if a.get("type") not in ["Ride", "VirtualRide", "Run"]:
            continue
            
        s = a.get("average_speed")
        hr = a.get("average_heartrate")
        if s and hr and 2 < s < 8 and 80 < hr < 180:
            vals.append((s*3.6)*0.2 + 3.5)
    if len(vals) >= 3:
        v = round(sum(vals)/len(vals),1)
        return v if v >= 20 else None
    return None

# --- FITNESS AGE ---
def fitness_age(rhr, hrv, vo2=None, fat=18.3): # Жир 18.3 из твоего лога S2
    try:
        actual_age = 63
        # 1. Влияние пульса покоя (RHR)
        rhr_val = int(rhr) if rhr and rhr != "Н/Д" else 60
        rhr_impact = (rhr_val - 55) * 0.4
        
        # 2. Влияние жира (Body Fat) - атлетический уровень
        fat_val = float(fat)
        fat_impact = (fat_val - 22) * 0.5
        
        # 3. Влияние HRV
        hrv_val = int(hrv) if hrv and hrv != "Н/Д" else 45
        hrv_impact = (hrv_val - 45) * 0.1

        # 4. Влияние VO2max (Новое!)
        vo2_impact = 0
        if vo2 and isinstance(vo2, (int, float)):
            # Чем выше VO2max, тем больше вычитаем из возраста (бонус до -5 лет)
            vo2_impact = (vo2 - 40) * 0.5

        # Итоговый расчет
        calculated = actual_age + rhr_impact + fat_impact - hrv_impact - vo2_impact
        
        # Ограничиваем разумными пределами
        return round(max(45, min(actual_age + 2, calculated)), 1)
    except:
        return 63

# --- AI ---
def ask_arnie(prompt, fallback_text):
    try:
        # 1. Сначала узнаем, какие модели сейчас живы
        res_m = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}", 
            timeout=10
        )
        models_data = res_m.json()
        available = [
            m["name"] for m in models_data.get("models", []) 
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
        
        if not available:
            return fallback_text
            
        # 2. Выбираем Flash (она быстрее и стабильнее)
        target_model = next((m for m in available if "flash" in m), available[0])
        
        # 3. Делаем сам запрос
        url = f"https://generativelanguage.googleapis.com/v1beta/{target_model}:generateContent?key={GEMINI_API_KEY}"
        res_ai = requests.post(
            url, 
            json={"contents": [{"parts": [{"text": prompt}]}]}, 
            timeout=30
        )
        
        data = res_ai.json()
        if "candidates" in data and data["candidates"]:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
            
    except Exception as e:
        print(f"AI Error: {e}")
        
    return fallback_text

# --- READINESS CALCULATION ---
def get_readiness(morning, tsb=0):
    # Базовая готовность 3.0
    readiness_score = 3.0
    
    try:
        # 1. HRV (Вес высокий)
        hrv_val = morning.get("HRV")
        if hrv_val and hrv_val != "Н/Д":
            val = int(hrv_val)
            if val > 75: readiness_score += 0.5
            elif val < 50: readiness_score -= 1.0

        # 2. ПУЛЬС ПОКОЯ (Resting HR)
        rhr_val = morning.get("Resting_HR")
        if rhr_val and rhr_val != "Н/Д":
            val = int(rhr_val)
            if 0 < val < 50: readiness_score += 0.5
            elif val > 60: readiness_score -= 0.5

        # 3. BODY BATTERY
        bb_val = morning.get("Body_Battery")
        if bb_val:
            val = int(bb_val)
            if val > 80: readiness_score += 0.5
            elif val < 40: readiness_score -= 1.0 # Если села батарейка — это серьезно

        # 4. СОН И ФАЗЫ (Новое)
        sleep_hrs = float(str(morning.get("Sleep_Hours", 0)).replace(',', '.'))
        deep_h = float(str(morning.get("Deep_Sleep", 0)).replace(',', '.'))
        s_score = int(morning.get("Sleep_Score", 100))
        
        if sleep_hrs < 5.5: readiness_score -= 1.0
        if deep_h < 0.7: readiness_score -= 0.8  # Штраф за дефицит глубокого сна
        if s_score < 65: readiness_score -= 1.0  # Штраф от Garmin за качество сна
        
        # 5. ВРЕМЯ ВОССТАНОВЛЕНИЯ (Новое)
        rec_time = int(morning.get("Recovery_Time", 0))
        if rec_time > 24: readiness_score -= 1.0
        if rec_time > 36: readiness_score -= 1.0 # Суммарно -2 если больше 36ч

        # 6. ФОРМА (TSB)
        t_val = float(tsb) if tsb else 0
        if t_val < -25: readiness_score -= 1.5
        elif -20 <= t_val <= -5: readiness_score += 0.5

    except Exception as e:
        print(f"Readiness Error: {e}")

    # Ограничиваем рамками 0 и 5
    readiness_score = max(0, min(5, round(readiness_score, 1)))
    
    # Интерпретация и иконки
    if readiness_score >= 4:
        text, icon = "Отличная готовность — идеальный день для рекордов", "🔥🏆"
    elif readiness_score >= 2.8:
        text, icon = "Хорошая готовность — можно тренироваться уверенно", "🟢🟢"
    elif readiness_score >= 1.5:
        text, icon = "Средняя готовность — требуются осторожность", "🟡🟠"
    else:
        text, icon = "Критическая усталость — строгий отдых", "🔴🔴"
    
    return readiness_score, text, icon

# --- MAIN ---
def main():
    now = datetime.utcnow() + timedelta(hours=1)
    today = now.strftime("%Y-%m-%d")
    yesterday = (datetime.strptime(today, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")

    activities = get_strava_data()
    morning = get_morning_metrics(today)

    # --- 1. РАСЧЕТ TSB (EMA Модель) ---
    # Сортируем активности от старых к новым для правильного накопления
    sorted_acts = sorted(activities, key=lambda x: x.get("start_date_local", "2000-01-01"))
    
    ctl, atl = 0, 0
    for a in sorted_acts:
        tss_val = calc_tss(a)
        # Формула EMA (экспоненциальное сглаживание)
        ctl += (tss_val - ctl) / 42
        atl += (tss_val - atl) / 7
    
    tsb = round(ctl - atl, 1)

    # --- 2. МЕТРИКИ И ГОТОВНОСТЬ ---
    rhr = morning.get("Resting_HR", "Н/Д")
    hrv = morning.get("HRV", "Н/Д")

    # Считываем новые данные для анализа (важно для корректного prompt)
    try:
        deep_sleep = morning.get("Deep_Sleep", 0)
        rem_sleep = morning.get("REM_Sleep", 0)
        sleep_score = morning.get("Sleep_Score", 0)
        recovery_h = morning.get("Recovery_Time", 0)
        acute_load = morning.get("Acute_Load", 0)
    except:
        deep_sleep, rem_sleep, sleep_score, recovery_h, acute_load = 0, 0, 0, 0, 0
    
    # Вот здесь мы добавляем расчет VO2max и передаем его в Fitness Age
    vo2_val = estimate_vo2max(activities)
    f_age = fitness_age(rhr, hrv, vo2_val) 

    # Записываем результат AI обратно в таблицу за сегодняшнюю дату
    update_fitness_age(today, f_age)
    
    # Считаем готовность один раз для всех отчетов
    r_val, r_text, r_icon = get_readiness(morning, tsb=tsb)

    # Фильтруем тренировки за сегодня
    today_acts = [a for a in activities if a.get("start_date_local", "")[:10] == today]

    # --- 2. УТРЕННИЙ РЕЖИМ ---
    if not today_acts:
        # Передаем полученный TSB в расчет готовности
        r_val, r_text, r_icon = get_readiness(morning, tsb=tsb)
        prompt = (
            f"Ты — легендарный Арнольд, элитный коуч и спортивный директор. "
            f"Твоя задача: дать хлесткий, профессиональный анализ утреннего состояния. Тон: харизматичный, суровый тренер. "
            f"ДАННЫЕ: "
            f"HRV {hrv}, Пульс {rhr}, Сон {morning.get('Sleep_Hours')}ч (Глубокий: {deep_sleep}ч), "
            f"Sleep Score: {sleep_score}/100, Recovery Time: {recovery_h}ч, "
            f"Body Battery: {morning.get('Body_Battery')}, Fit Age: {f_age}, VO2max: {vo2_val if vo2_val else 'н/д'}. "
            f"ФОРМА: CTL {round(ctl,1)} (Фитнес), TSB {tsb} (Баланс). "
            f"ИТОГОВАЯ ГОТОВНОСТЬ: {r_val}/5. "
            f"\nИНСТРУКЦИИ: "
            f"1. Не перечисляй цифры, а интерпретируй их! ПИШИ НА РУССКОМ. "
            f"2. ПРИОРИТЕТ: Если Recovery Time {recovery_h} > 24ч или Sleep Score {sleep_score} < 65 — СТРОГО ЗАПРЕТИ рекорды. "
            f"3. Если HRV {hrv} > 70 и Пульс {rhr} < 50 — отметь крутую работу сердца, но сопоставь это с качеством сна. "
            f"4. Трактуй TSB {tsb}: >10 (застой), -10...-25 (зона чемпионов, работаем), < -25 (риск травм). "
            f"5. ДАЙ КОНКРЕТНЫЙ ПЛАН: укажи зону (Z1, Z2 или Отдых) и время в минутах. "
            f"6. Если Fit Age {f_age} ниже 63 — вставь мощный комментарий об этом. "
            f"7. Будь лаконичен (2-3 абзаца), сохрани дух Терминатора. "
            f"В конце — одна фирменная фраза."
        )
        ai_response = ask_arnie(prompt, r_text)

        report = (
            f"🌅 *УТРЕННИЙ СТАТУС* {r_icon}\n\n"
            f"❤️ Пульс: {rhr} | 🌀 HRV: {hrv}\n"
            f"🔋 *Готовность: {r_val}/5*\n"
            f"📊 Форма (TSB): {tsb} | VO2max: {vo2_val if vo2_val else 'н/д'}\n"
            f"📢 {r_text}\n"
            f"🧬 Fitness Age: {f_age}\n\n"
            f"🤖 *АРНИ:* \n_{ai_response}_"
        )
        send_tg(report)
        print("✅ MORNING REPORT SENT")
        return

    # --- 3. АНАЛИЗ ТРЕНИРОВКИ ---
    last = sorted(today_acts, key=lambda x: x.get("start_date_local"))[-1]
    tss_last = calc_tss(last)
    dist = round(last.get("distance", 0) / 1000, 2)
    name = last.get("name", "Тренировка")

    prompt = (
        f"Ты — опытный коуч Арнольд. Профессиональный разбор сессии: {name}. "
        f"Дистанция: {dist}км, TSS: {tss_last}. Пульс в покое утром был {rhr}. "
        f"Контекст: Готовность была {r_val}/5, TSB {tsb}. "
        f"\nИНСТРУКЦИИ: "
        f"1. Оцени качество работы. "
        f"2. ПИШИ СТРОГО НА РУССКОМ, в своем стиле, но БЕЗ ЛИШНЕЙ ВОДЫ. "
        f"3. Дай краткий совет на завтра."
    )
    ai_response = ask_arnie(prompt, "Хорошая работа.")

    report = (
        f"🏃 *ТРЕНИРОВКА*\n\n"
        f"*{name}*\n"
        f"📍 {dist} км | 📈 TSS {tss_last}\n"
        f"🧬 Fitness Age: {f_age}\n\n"
        f"🤖 *АРНИ:* \n_{ai_response}_"
    )
    send_tg(report)
    print("✅ TRAINING REPORT SENT")

if __name__ == "__main__":
    main()
