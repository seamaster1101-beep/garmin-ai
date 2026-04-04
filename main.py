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
    s_val = str(val).replace(',', '.').replace('\xa0', '').strip()
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
                header = [h.replace('\xa0', '').strip() for h in header]
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

    # Metrics (Ключи строго как в заголовках таблицы)
    rhr = safe_float(morning.get("Resting_HR"), 60)
    hrv = safe_float(morning.get("HRV"), 45)
    weight = safe_float(morning.get("Weight"), 88.0)
    if weight > 500: weight /= 10
    fat = safe_float(morning.get("Body_Fat"), 18.3)
    if fat > 100: fat /= 10
    
    sleep = safe_float(morning.get("Sleep_Hours"), 7.0)
    if sleep > 24: sleep /= 10
        
    ds_val = safe_float(morning.get("Deep_Sleep"), 0.0)

    if 0 < ds_val < 1.0:
        deep_sleep = round(sleep * ds_val, 1)
    else:
        deep_sleep = ds_val

    if deep_sleep >= sleep and sleep > 0:
        deep_sleep = round(sleep * 0.25, 1)

    sleep_score = int(safe_float(morning.get("Sleep_Score"), 0)) 
    recovery_h = int(safe_float(morning.get("Recovery_Time"), 0))
        
    # Расчет производительности (обязательно!)
    vo2_val, eftp_val = estimate_performance(activities, weight=weight)
    
    # Оставляем только один расчет today_acts здесь
    today_acts = [a for a in activities if a.get("start_date_local", "")[:10] == today and a.get("type") not in ["Walk", "Hike"]]

    ctl, atl = 0, 0

    # 2. Цикл накопления (проходим по всей истории)
    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        # ИСКЛЮЧАЕМ тренировки за сегодня для фиксации утренней формы
        if a.get("start_date_local", "")[:10] == today:
            continue
            
        tss = 0
        a_type = a.get("type")
        t_sec = a.get("moving_time", 0)

        if a_type in ["Ride", "VirtualRide"]:
            w = safe_float(a.get("average_watts"), 0)
            tss = (t_sec/3600)*(w/FTP_GARMIN)**2*100 if w > 0 else 0
        # Расширенный список для силовых
        elif a_type in ["Weight Training", "Workout", "WeightTraining", "Gym"]:
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

    # Мы берем переменную sleep, которая уже была рассчитана выше в коде
    sleep_hours = sleep

    # Данные для расчета (убедись, что они определены выше)
    hrv_7d_avg = 85 # Временная заглушка, пока не настроим авто-расчет

    # --- 3. ПЕРСОНАЛИЗИРОВАННЫЙ РАСЧЕТ ГОТОВНОСТИ (v2.1) & FitAge
    score = 3.5  # База: учитываем отличный Fitness Age 48

    # HRV: Вариабельность (твой диапазон 30-128)
    if hrv > 95:
        score += 1.0
    elif hrv > 75:
        score += 0.5
    elif 40 <= hrv <= 75:
        # В рабочей зоне смотрим на тренд относительно недели
        if hrv < hrv_7d_avg * 0.8:
            score -= 0.5
        elif hrv > hrv_7d_avg * 1.1:
            score += 0.3
    elif hrv < 40:
        # Умный штраф: если пульс в норме, значит это усталость, а не катастрофа
        score -= 1.0 if rhr >= 55 else 0.5

    # RHR: Пульс покоя (твой диапазон 46-54)
    if rhr <= 50:
        score += 0.5
    elif 51 <= rhr <= 54:
        pass # Идеальное попадание в норму
    elif rhr >= 55:
        score -= 0.7

    # Сон (Качество: Sleep Score)
    if 0 < sleep_score < 50:
        score -= 1.2 if rhr >= 55 else 0.7
    elif 50 <= sleep_score < 65:
        score -= 0.5
    elif 65 <= sleep_score < 80:
        score += 0.2
    elif sleep_score >= 80:
        score += 0.5

    # Сон (Продолжительность: Sleep Hours)
    if sleep_hours < 6:
        score -= 0.5

    # Recovery Time (Время восстановления)
    if recovery_h > 48:
        score -= 0.8
    elif recovery_h > 24:
        score -= 0.3

    # Форма (TSB / Acute Load)
    if tsb < -20:
        score -= 1.0 if hrv < 60 else 0.5
    elif -10 <= tsb <= 5:
        score += 0.3

    # Финальное ограничение диапазона [0...5]
    score = max(0.0, min(5.0, round(score, 1)))
    
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
    f_age = round(max(48.0, min(get_bio_age() - 2, f_age)), 1)

    update_fitness_age_in_sheet(today, f_age)

    # 6. --- ПРОМПТ И ОТЧЕТ (VERBATIM GITHUB) ---# Report

    if not today_acts:
        status_icon = "🔥🏆" if score >= 4 else "🟢🟢" if score >= 2.8 else "🟡"
        sleep_note = f"- ВАЖНО: Сон < 6.5ч. Жестко снижай интенсивность, запрети агрессивную Зону 3 (Z3).\n" if sleep < 6.5 else ""

    prompt = (
        f"Ты — АРНИ, стиль: жесткий, лаконичный, уверенный тренер. "
        f"Без хамства, без крика, без панибратства. "
        f"НЕ начинай с фраз типа: 'Слушай сюда', 'Чемпион', 'Боец'. "
        f"Атлет: {round(get_bio_age())} лет. "

        f"ДАННЫЕ: HRV {int(hrv)}, Пульс {int(rhr)}, Сон {sleep}ч (Глубокий: {deep_sleep}ч), "
        f"Sleep Score: {sleep_score}, Recovery: {recovery_h}ч, TSB {tsb}, Готовность {score}/5. "
        f"Fit Age {f_age}. VO2max: {vo2_val}. "

        f"\nПРАВИЛА АНАЛИЗА:\n"
        f"{sleep_note}"

        f"- ЛИЧНЫЕ ДИАПАЗОНЫ:\n"
        f"  RHR: 46–54 (норма), <50 отлично, >55 сигнал усталости.\n"
        f"  HRV: <40 низко; 40–70 нижняя зона; 70–95 норма; >95 пик готовности.\n"

        f"- Если HRV и пульс в норме (RHR ≤54 и HRV ≥70), не называй состояние истощением.\n"
        f"- HRV <40 — это усталость, но не катастрофа, если пульс в норме.\n"
        f"- Низкий сон — ограничение восстановления, а не обнуление формы.\n"
        f"- Не обесценивай сильные показатели (Fit Age, пульс, VO2max).\n"
        f"- Если HRV >95 — допускается повышение нагрузки.\n"

        f"- TSB около 0 — это баланс, не отдых и не перегруз.\n"

        f"- Контроль зон: <2.0 отдых; 2.0–3.0 Z1–Z2; 3.0–3.5 осторожно Z2; >3.5 можно Z3.\n"

        f"- Каждый пункт — максимум 2-3 коротких предложения.\n"
        f"- Строго соблюдай формат из 3 пунктов.\n"
        f"- Сразу начинай с пункта 1, без вступления.\n"
        f"- Финальная фраза — короткая, на русском, без английского.\n"

        f"\nВЫДАЙ СТРОГО ПО ПУНКТАМ:\n"
        f"1. СОСТОЯНИЕ: Связка HRV и TSB.\n"
        f"2. АНАЛИЗ: Оценка базы (Fit Age, RHR, VO2max).\n"
        f"3. ВЕРДИКТ: План на день (зона и время).\n"
    )
    ai_msg = ask_arnie(prompt, "1. СОСТОЯНИЕ: Данные получены.\n2. АНАЛИЗ: База стабильна.\n3. ВЕРДИКТ: Держи план дня по готовности.")


        # 1. Сначала определяем текстовый статус (s_status)
        if sleep_score < 55:
            s_status = "Плохо"
        elif sleep_score < 75:
            s_status = "Средне"
        else:
            s_status = "Отлично"

        # 2. И только потом используем его в отчете
        
        report = (f"🌅 УТРЕННИЙ СТАТУС {status_icon} | FTP: {FTP_GARMIN}{eftp_str}\n\n"
                  f"❤️ Пульс: {int(rhr)} | 🌀 HRV: {int(hrv)}\n"
                  f"🔋 Готовность: {score}/5\n"
                  f"😴 Качество сна: {sleep_score} ({s_status})\n"
                  f"📊 Форма (TSB): {tsb} | VO2max: {vo2_val if vo2_val else 'н/д'}\n"
                  f"🧬 Fit Age: {f_age}\n\n"
                  f"🤖 АРНИ:\n{ai_msg}")

    else:
        # --- АНАЛИЗ ТРЕНИРОВКИ (v2.2) ---
        last = sorted(today_acts, key=lambda x: x.get("start_date_local"))[-1]
        dist = round(last.get("distance", 0) / 1000, 2)
        name = last.get("name", "Тренировка")
        t_sec = last.get("moving_time", 0)
        dur_min = round(t_sec / 60, 1)
        a_type_last = last.get("type")
        
        # Собираем данные интенсивности
        w_avg = last.get("average_watts", 0)
        hr_avg = last.get("average_heartrate", 0)
        hr_max_act = last.get("max_heartrate", 0)
        
        # Расчет TSS
        if a_type_last in ["Ride", "VirtualRide"]:
            tss_last = round((t_sec/3600)*(w_avg/FTP_GARMIN)**2*100, 1) if w_avg else 0
        elif a_type_last in ["Weight Training", "Workout", "WeightTraining", "Gym"]:
            tss_last = round((t_sec / 60) * 0.6, 1)
        else:
            tss_last = 0

        # Формируем умный промпт для анализа нагрузки
        prompt = (
            f"Ты — АРНИ, стиль: коротко, точно, уважающий усилия. Разбери тренировку атлета 63 лет: {name}. "
            f"ДАННЫЕ: Длительность {dur_min} мин, Дистанция {dist} км, TSS {tss_last}. "
            f"Пульс ср/макс: {hr_avg}/{hr_max_act}. Утренняя готовность была {score}/5. "
            f"\nПРАВИЛА АНАЛИЗА:\n"
            f"- Если это велотренировка и есть данные о пульсе/мощности, не делай вывод только по TSS. "
            f"- Если работа велась на высоком пульсе или мощности (Z4), называй её 'пороговой' или 'интенсивной', а не прогулкой. "
            f"- Силовую тренировку ({dur_min} мин) оценивай как рабочий стимул, а не отдых. "
            f"- Будь жестким в плане дисциплины, но признавай реальный труд. "
            f"\nОТВЕТЬ СТРОГО ПО ПУНКТАМ:\n"
            f"1. СТАТУС: Реальная интенсивность нагрузки.\n"
            f"2. ФИДБЕК: Что было сделано хорошо.\n"
            f"3. ЗАВТРА: Конкретный совет по восстановлению.\n"
            f"\nБез вступлений. В конце — одна фраза Арнольда. На русском."
        )
        ai_msg = ask_arnie(prompt, "Тренировка зафиксирована. Анализ будет позже.")

        report = (f"🏃 ТРЕНИРОВКА {status_icon} | FTP: {FTP_GARMIN}\n\n"
                  f"<b>{name}</b>\n"
                  f"📍 {dist} км | ⏱ {dur_min} мин | 📈 TSS: {tss_last}\n"
                  f"🧬 Fit Age: {f_age}\n\n"
                  f"🤖 АРНИ:\n{ai_msg}")

if __name__ == "__main__":
    main()
