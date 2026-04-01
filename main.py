import os, requests, json, sys, gspread
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# --- CONFIG ---
BIRTH_DATE = datetime(1963, 5, 29)
FTP_GARMIN = 213 
SPREADSHEET_ID = "1rxg5oqDXWXwHSHMmR-RbJuad8rXe2OdmCEMUMY2SBT4"

def get_bio_age():
    return (datetime.utcnow() - BIRTH_DATE).days / 365.25

def get_env(name):
    val = os.environ.get(name)
    if not val:
        print(f"❌ Нет переменной: {name}"); sys.exit(1)
    return val

# Переменные окружения
CLIENT_ID = get_env('STRAVA_CLIENT_ID')
CLIENT_SECRET = get_env('STRAVA_CLIENT_SECRET')
REFRESH_TOKEN = get_env('STRAVA_REFRESH_TOKEN')
TELEGRAM_BOT_TOKEN = get_env('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = get_env('TELEGRAM_CHAT_ID')
GEMINI_API_KEY = get_env('GEMINI_API_KEY')
GOOGLE_CREDS_JSON = get_env('GOOGLE_CREDS')

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def safe_float(val, default=0.0):
    if val is None: return default
    # Превращаем в строку, убираем пробелы и заменяем запятую на точку
    s_val = str(val).replace(',', '.').strip()
    if s_val in ["", "Н/Д", "None"]: return default
    try:
        v = float(s_val)
        return v if v >= 0 else default
    except:
        return default

def send_tg(msg):
    if len(msg) > 4000: msg = msg[:3900]
    try:
        res = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                     json={"chat_id": TELEGRAM_CHAT_ID, "text": msg}, timeout=15)
        if res.status_code != 200: print(f"⚠️ TG Error: {res.text}")
    except Exception as e: print(f"❌ TG Exception: {e}")

def ask_arnie(prompt, fallback_text):
    try:
        # 1. Сначала узнаем, какие модели сейчас доступны
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
            
        # 2. Выбираем Flash (она быстрее и стабильнее для таких задач)
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
            # Убираем лишние символы форматирования, которые иногда мешают в Telegram
            return data["candidates"][0]["content"]["parts"][0]["text"].strip().replace("_", " ").replace("*", " ")
            
    except Exception as e:
        print(f"⚠️ AI Error: {e}")
        
    return fallback_text


# --- РАБОТА С ДАННЫМИ ---
def get_google_client():
    creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), 
                                                  scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return gspread.authorize(creds)

def update_fitness_age_in_sheet(target_date, f_age_val):
    try:
        client = get_google_client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        dates = sheet.col_values(1)
        for i, val in enumerate(dates):
            if target_date in val:
                header = sheet.row_values(1)
                if "Fitness_Age" in header:
                    sheet.update_cell(i + 1, header.index("Fitness_Age") + 1, f_age_val)
                    print(f"✅ FitAge {f_age_val} записан.")
                    break
    except Exception as e: print(f"⚠️ Sheet update error: {e}")

def estimate_performance(activities, weight=88.0):
    vals_vo2 = []
    hr_max = 208 - (0.7 * get_bio_age())
    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        if a.get("type") not in ["Ride", "VirtualRide"]: continue
        w = safe_float(a.get("average_watts"), 0)
        hr = safe_float(a.get("average_heartrate"), 0)
        if w > 10 and hr > 105:
            v = (10.51 * (w * (hr_max / hr)) / weight) + 7
            if 20 < v < 65: vals_vo2.append(v)
    
    if not vals_vo2: return None, None
    avg_vo2 = round(sum(vals_vo2[-7:]) / len(vals_vo2[-7:]), 1)
    # Ограничение eFTP 100-400 ватт
    eftp = max(100, min(400, int(round(avg_vo2 * weight * 0.071, 0))))
    return avg_vo2, eftp

# --- MAIN ---
def main():
    now = datetime.utcnow() + timedelta(hours=2)
    today = now.strftime("%Y-%m-%d")
    
    # Strava Data
    activities = []
    try:
        res = requests.post("https://www.strava.com/oauth/token", data={
            'client_id': CLIENT_ID, 'client_secret': CLIENT_SECRET,
            'refresh_token': REFRESH_TOKEN, 'grant_type': 'refresh_token'
        }, timeout=15)
        
        try:
            token_data = res.json()
        except Exception:
            print("❌ Ошибка: Strava вернула не JSON")
            token_data = {}

        token = token_data.get('access_token')

        if not token:
            print(f"❌ Strava token error: {token_data}")
        else:
            r = requests.get("https://www.strava.com/api/v3/athlete/activities",
                             headers={"Authorization": f"Bearer {token}"}, 
                             params={"per_page": 100}, timeout=15)
            data = r.json()
            if isinstance(data, list):
                activities = data
            else:
                print(f"⚠️ Strava API вернул ошибку: {data}")
                activities = []
            
    except Exception as e: 
        print(f"❌ Strava fail: {e}")

    # Google Sheets Data
    morning = {}
    try:
        client = get_google_client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        records = sheet.get_all_records()
        
        if records:
            morning = next(
                (row for row in reversed(records) if today in str(row.get('Date', ''))), 
                records[-1]
            )
    except Exception as e: 
        print(f"❌ Sheets fail: {e}")

    # Metrics
    rhr = safe_float(morning.get("Resting_HR"), 60)
    hrv = safe_float(morning.get("HRV"), 45)
    weight = safe_float(morning.get("Weight"), 88.0)
    if weight > 500: weight /= 10
    fat = safe_float(morning.get("Body_Fat"), 18.3)
    if fat > 100: fat /= 10
    
    # Расширенные метрики сна и восстановления
    sleep = safe_float(morning.get("Sleep_Hours"), 7.0)
    if sleep > 24: sleep /= 10
        
    # Читаем глубокий сон (теперь safe_float точно съест запятую)
    ds_val = safe_float(morning.get("Deep_Sleep"), 0.0)

    # Если число меньше 1.0 (например 0.6), пересчитываем в часы от общего сна
    if 0 < ds_val < 1.0:
        deep_sleep = round(sleep * ds_val, 1)
    else:
        deep_sleep = ds_val

    # Финальная страховка, чтобы не было "невозможных данных" для ИИ
    if deep_sleep >= sleep and sleep > 0:
        deep_sleep = round(sleep * 0.25, 1) # Если баг, ставим 25% от общего
        
    sleep_score = int(safe_float(morning.get("Sleep_Score"), 0))
    recovery_h = int(safe_float(morning.get("Recovery_Time"), 0))

    # Расчет производительности (обязательно!)
    vo2_val, eftp_val = estimate_performance(activities, weight=weight)
    
    # Оставляем только один расчет today_acts здесь
    today_acts = [a for a in activities if a.get("start_date_local", "")[:10] == today]

    ctl, atl = 0, 0
    # 2. Цикл накопления (проходим по всей истории)
    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        tss = 0
        a_type = a.get("type")
        t_sec = a.get("moving_time", 0)

        if a_type in ["Ride", "VirtualRide"]:
            w = safe_float(a.get("average_watts"), 0)
            tss = (t_sec/3600)*(w/FTP_GARMIN)**2*100 if w > 0 else 0
        elif a_type in ["Weight Training", "Workout"]:
            tss = (t_sec / 60) * 0.6
        
        # ВАЖНО: Это то, что ты удалил. Без этого CTL/ATL всегда будут 0
        if tss > 0:
            ctl += (tss - ctl) / 42
            atl += (tss - atl) / 7

    # 3. ВАЖНО: Выходим из цикла (убираем отступ!)
    # Если за сегодня не было новых тренировок, применяем затухание
    if not today_acts:
        ctl *= 0.98
        atl *= 0.90
    
    tsb = round(ctl - atl, 1)

    # Readiness & FitAge
    score = 3.0  # Базовая точка

    # 1. HRV — Твоя суперсила
    if hrv > 95: 
        score += 1.5   # Рекордный отскок (как сегодня 102)
    elif hrv > 75: 
        score += 0.5
    elif hrv < 50: 
        score -= 1.0

    # 2. Пульс покоя (RHR)
    if rhr < 50: 
        score += 0.5
    elif rhr > 60: 
        score -= 0.5

    # 3. Качество сна (Sleep Score)
    if 0 < sleep_score < 55: 
        score -= 1.5   # Плохо
    elif 55 <= sleep_score < 65:
        score -= 1.0   # Ниже среднего
    elif 65 <= sleep_score < 80: 
        score -= 0.5   # Нормально
    elif sleep_score >= 80: 
        score += 0.5   # Хорошо / отлично

    # 4. Время восстановления (Recovery Time)
    if recovery_h > 24:
        if hrv > 90:
            score -= 0.5  # Если HRV высокий, тело справляется быстрее (смягчаем штраф)
        else:
            score -= 1.0  # Обычный штраф за недовосстановление

    # 5. Форма (TSB)
    if tsb < -20:
        if hrv > 85:
            score -= 0.5
        else:
            score -= 1.5

    elif -20 <= tsb < -10:
        if hrv > 85:
            score += 0.5   # суперкомпенсация
        else:
             score -= 0.5

    elif -10 <= tsb <= -5:
        score += 0.5

    # Финальный зажим в рамки 0-5
    score = max(0, min(5, round(score, 1)))
    
    icon = "🔥🏆" if score >= 4 else "🟢🟢" if score >= 2.8 else "🟡"
    circles = "🟢🟢🟢" if score >= 4 else "🟢🟢" if score >= 2.8 else "🟡"

    # --- 1. БАЗОВАЯ ФОРМА (Долгосрочная) ---
    vo2_calc = vo2_val if vo2_val else 32.7

    # Добавляем влияние пульса (чем ниже — тем моложе)
    rhr_factor = (51 - rhr) * 0.3

    base_age = (
        get_bio_age()
        + (fat - 22) * 0.5
        - (vo2_calc - 32) * 2.0
        - rhr_factor
    )

    # --- 2. КОРРЕКЦИЯ СОСТОЯНИЯ (Краткосрочная) ---
    # HRV — смягчаем влияние (убираем "жадность")
    hrv_dev = (hrv - 85) / 85
    hrv_penalty = max(-0.7, min(0.7, -hrv_dev * 2))

    # Пульс — лёгкая коррекция (не дублируем сильно base)
    rhr_penalty = max(-0.3, min(0.3, (rhr - 51) * 0.04))

    # Сон — уменьшаем штраф
    sleep_p = 0.7 if 0 < sleep_score < 60 else 0.3 if sleep_score < 75 else 0

    # --- 3. ИТОГ ---
    f_age = round(base_age + hrv_penalty + rhr_penalty + sleep_p, 1)

    # Более реалистичный диапазон
    f_age = max(48.0, min(get_bio_age() - 2, f_age))

    update_fitness_age_in_sheet(today, f_age)

    # Report
    eftp_str = f" | eFTP: {eftp_val} ({eftp_val - FTP_GARMIN:+})" if eftp_val else ""
        
    if not today_acts:
        prompt = (
            f"Ты — АРНИ, элитный спортивный директор. Твой стиль: лаконичный, мудрый, прямолинейный. "
            f"Атлет: 63 года. ДАННЫЕ: HRV {int(hrv)}, Пульс {int(rhr)}, Сон {sleep}ч (Глубокий: {deep_sleep}ч), "
            f"Sleep Score: {sleep_score}, Recovery: {recovery_h}ч, TSB {tsb}, Готовность {score}/5. "
            f"Fit Age {f_age}. VO2max: {vo2_val if vo2_val else 'н/д'}. "
            f"\nИНСТРУКЦИИ: "
            f"Выдай анализ строго по пунктам: "
            f"1. СОСТОЯНИЕ: Связка HRV и TSB. Если HRV > 90 (сейчас {int(hrv)}), это фаза суперкомпенсации — разрешай работу, несмотря на усталость. "
            f"2. АНАЛИЗ: Сравни Fit Age {f_age} и пульс {int(rhr)} с активными ровесниками (уровень профи). Если глубокий сон {deep_sleep}ч мал — укажи на риск для ЦНС. "
            f"3. ВЕРДИКТ: Четкий план на день (Z-зона и время). "
            f"\nБез лишних приветствий. В конце — одна легендарная фраза Арнольда. Пиши на русском."
            f"Пиши строго на русском."
        )
        ai_msg = ask_arnie(prompt, "В строю. Жду работу.")

        # 1. Сначала определяем текстовый статус (s_status)
        if sleep_score < 55:
            s_status = "Плохо"
        elif sleep_score < 75:
            s_status = "Средне"
        else:
            s_status = "Отлично"

        # 2. И только потом используем его в отчете
        
        report = (f"🌅 УТРЕННИЙ СТАТУС {icon}\n"
                  f"{circles} FTP: {FTP_GARMIN}{eftp_str}\n\n"
                  f"❤️ Пульс: {int(rhr)} | 🌀 HRV: {int(hrv)}\n"
                  f"🔋 Готовность: {score}/5\n"
                  f"😴 Качество сна: {sleep_score} ({s_status})\n"
                  f"📊 Форма (TSB): {tsb} | VO2max: {vo2_val if vo2_val else 'н/д'}\n"
                  f"🧬 Fit Age: {f_age}\n\n"
                  f"🤖 АРНИ:\n{ai_msg}")

    else:
        # Анализ последней тренировки за сегодня
        last = sorted(today_acts, key=lambda x: x.get("start_date_local"))[-1]
        dist = round(last.get("distance", 0) / 1000, 2)
        name = last.get("name", "Тренировка")
        
        # Для расчета TSS нам нужен FTP_GARMIN
        w_avg = last.get("average_watts", 0)
        t_sec = last.get("moving_time", 0)
        a_type_last = last.get("type")
        if a_type_last in ["Ride", "VirtualRide"]:
            tss_last = round((t_sec/3600)*(w_avg/FTP_GARMIN)**2*100, 1) if w_avg else 0
        elif a_type_last in ["Weight Training", "Workout"]:
            tss_last = round((t_sec / 60) * 0.6, 1)
        else:
            tss_last = 0

        prompt = (
            f"Ты — АРНИ. Разбери тренировку атлета 63 лет: {name}. "
            f"Данные: {dist}км, TSS {tss_last}. Утренняя готовность была {score}/5. "
            f"\nИНСТРУКЦИИ: "
            f"1. СТАТУС: Оцени адекватность нагрузки ({tss_last} TSS) текущему состоянию и возрасту. "
            f"2. ФИДБЕК: Коротко похвали за дисциплину или укажи на халтуру, если нагрузка символическая. "
            f"3. ЗАВТРА: Дай одну конкретную рекомендацию на следующий день. "
            f"\nПиши лаконично, без воды. В конце — одна фраза Арнольда. На русском."
        )
        ai_msg = ask_arnie(prompt, "Работа сделана. Отдыхай.")

        report = (f"🏃 ТРЕНИРОВКА {icon}\n"
                  f"{circles} FTP: {FTP_GARMIN}\n\n"
                  f"{name}\n"
                  f"📍 {dist} км | 📈 TSS: {tss_last}\n"
                  f"🧬 Fit Age: {f_age}\n\n"
                  f"🤖 АРНИ:\n{ai_msg}")

    send_tg(report)

if __name__ == "__main__":
    main()
