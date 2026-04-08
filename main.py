import os, requests, json, sys, gspread, html
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

def get_hrv_14d_avg_from_sheet(sheet, today_str):
    try:
        all_values = sheet.get_all_values()
        if not all_values or len(all_values) < 2:
            return 85.0

        header = [str(h).replace('\xa0', '').strip() for h in all_values[0]]

        if "Date" not in header or "HRV" not in header:
            return 85.0

        date_idx = header.index("Date")
        hrv_idx = header.index("HRV")

        vals = []
        for row in reversed(all_values[1:]):
            if len(row) <= max(date_idx, hrv_idx):
                continue

            row_date = str(row[date_idx]).strip()
            row_hrv = safe_float(row[hrv_idx], 0)

            # исключаем сегодня
            if not row_date or today_str not in row_date:
                if row_hrv > 0:
                    vals.append(row_hrv)

            if len(vals) >= 14:
                break

        if vals:
            return round(sum(vals) / len(vals), 1)

    except Exception as e:
        print(f"⚠️ HRV 14d avg error: {e}")

    return 85.0

def send_tg(msg):
    if len(msg) > 4000: 
        msg = msg[:3900]
    try:
        res = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": msg,
                "parse_mode": "HTML"
            },
            timeout=15
        )
        if res.status_code != 200:
            print(f"⚠️ TG Error: {res.text}")
    except Exception as e:
        print(f"❌ TG Exception: {e}")

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
            return html.escape(
                data["candidates"][0]["content"]["parts"][0]["text"].strip().replace("_", " ").replace("*", " ")
            )    
            
    except Exception as e:
        print(f"⚠️ AI Error: {e}")
        
    return fallback_text


# --- РАБОТА С ДАННЫМИ ---
def get_google_client():
    creds = Credentials.from_service_account_info(json.loads(GOOGLE_CREDS_JSON), 
                                                  scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return gspread.authorize(creds)

def update_eftp_in_sheet(target_date, eftp_val):
    try:
        client = get_google_client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        dates = sheet.col_values(1)
        for i, val in enumerate(dates):
            if target_date in val:
                header = sheet.row_values(1)
                header = [h.replace('\xa0', '').strip() for h in header]

                target_column = "eFTP_Strava"

                if target_column in header:
                    sheet.update_cell(i + 1, header.index(target_column) + 1, eftp_val)
                    print(f"✅ eFTP_Strava {eftp_val} записан.")
                    break
    except Exception as e:
        print(f"⚠️ Sheet update error: {e}")

def update_morning_sheet(date_str, row_data):
    try:
        client = get_google_client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        dates = sheet.col_values(1)
        row_idx = None
        for i, val in enumerate(dates):
            if str(val).startswith(date_str):
                row_idx = i + 1
                break
        if row_idx:
            sheet.update(values=[row_data], range_name=f"A{row_idx}:R{row_idx}", value_input_option='USER_ENTERED')
            print(f"✅ Данные за {date_str} обновлены в таблице.")
        else:
            sheet.append_row(row_data, value_input_option='USER_ENTERED')
            print(f"✅ Добавлена новая запись за {date_str}.")
    except Exception as e:
        print(f"⚠️ Sheet update error: {e}")

def get_yesterday_recovery_from_sheet(target_date):
    try:
        client = get_google_client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        records = sheet.get_all_records()

        for row in reversed(records):
            row_date = str(row.get("Date", "")).replace("'", "").strip()
            if row_date.startswith(target_date):
                val = row.get("Recovery_Time")
                if val not in (None, "", "Н/Д"):
                    return int(float(val))
    except Exception as e:
        print(f"⚠️ Yesterday recovery read error: {e}")

    return None

def estimate_performance(activities, weight):
    vals_vo2 = []
    hr_max = 208 - (0.7 * get_bio_age())

    if not weight or weight <= 0:
        weight = 88.0

    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        if a.get("type") not in ["Ride", "VirtualRide"]:
            continue

        w = safe_float(a.get("average_watts"), 0)
        hr = safe_float(a.get("average_heartrate"), 0)

        if w > 10 and hr > 105:
            v = (10.51 * (w * (hr_max / hr)) / weight) + 7
            if 20 < v < 65:
                vals_vo2.append(v)

    if not vals_vo2:
        return None, None

    avg_vo2 = round(sum(vals_vo2[-7:]) / len(vals_vo2[-7:]), 1)
    eftp = max(100, min(400, int(round(avg_vo2 * weight * 0.071, 0))))
    return avg_vo2, eftp

def estimate_recovery_hours(acts, today_str, ftp, hrv, rhr, tsb):
    """
    Fallback-расчет Recovery Time, если Garmin не дал значение.

    Логика:
    1) если есть вчерашний Recovery_Time в таблице — продолжаем остаток
    2) если нет — считаем по всем вчерашним нетривиальным тренировкам
    """
    yesterday_str = (datetime.strptime(today_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
    y_recovery = get_yesterday_recovery_from_sheet(yesterday_str)

    # --- ВЕТКА 1: продолжаем вчерашний recovery ---
    if y_recovery is not None and y_recovery > 0:
        hours_passed = 8  # для утреннего запуска считаем, что за ночь списалось ~8 часов

        recovery_h = y_recovery - hours_passed

        # мягкая коррекция по утреннему состоянию
        if hrv > 95:
            recovery_h -= 1
        elif hrv < 50:
            recovery_h += 2

        if rhr <= 48:
            recovery_h -= 1
        elif rhr >= 55:
            recovery_h += 2

        if tsb < -10:
            recovery_h += 2
        elif tsb > 5:
            recovery_h -= 1

        return max(0, min(72, round(recovery_h)))

    # --- ВЕТКА 2: если нет вчерашнего recovery, считаем по ВСЕМ вчерашним тренировкам ---
    yesterday_acts = [
        a for a in acts
        if a.get("start_date_local", "")[:10] == yesterday_str
        and a.get("type") not in ["Walk", "Hike"]
    ]

    if not yesterday_acts:
        return 0

    base_rec = 0.0

    for a in yesterday_acts:
        a_type = a.get("type")
        t_sec = a.get("moving_time", 0) or 0

        if t_sec <= 0:
            continue

        if a_type in ["Ride", "VirtualRide"]:
            w_avg = safe_float(a.get("average_watts"), 0)

            if w_avg > 0 and ftp > 0:
                tss_last = (t_sec / 3600) * (w_avg / ftp) ** 2 * 100
                rec_add = tss_last * 0.65
            else:
                rec_add = (t_sec / 60) * 0.30

        elif a_type in ["Weight Training", "Workout", "WeightTraining", "Gym"]:
            rec_add = (t_sec / 60) * 0.08

        else:
            rec_add = (t_sec / 60) * 0.20

        base_rec += rec_add

    adj = 0

    if hrv < 40:
        adj += 8
    elif hrv < 60:
        adj += 4
    elif hrv > 95:
        adj -= 3
    elif hrv > 80:
        adj -= 1

    if rhr >= 55:
        adj += 4
    elif rhr <= 48:
        adj -= 2

    if tsb < -20:
        adj += 8
    elif tsb < -10:
        adj += 4
    elif tsb > 5:
        adj -= 2
    elif tsb >= 0:
        adj -= 1

    recovery_h = round(base_rec + adj)
    return max(0, min(72, recovery_h))

# --- MAIN ---
def main():
    now_dt = datetime.now()
    today = now_dt.strftime("%Y-%m-%d")
    yesterday_str = (now_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    
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
    records = []
    all_values = []
    sheet = None
    try:
        client = get_google_client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")

        all_values = sheet.get_all_values()
        if all_values and len(all_values) > 1:
           header = [str(h).replace('\xa0', '').strip() for h in all_values[0]]
           records = [
               {header[i]: row[i] if i < len(row) else "" for i in range(len(header))}
               for row in all_values[1:]
           ]

           for row in reversed(all_values[1:]):
                if row and today in str(row[0]):
                    morning = {header[i]: row[i] if i < len(row) else "" for i in range(len(header))}
                    break

           if not morning:
                last_row = all_values[-1]
                morning = {header[i]: last_row[i] if i < len(last_row) else "" for i in range(len(header))}

        
    except Exception as e:
        print(f"❌ Sheets fail: {e}")

    # Metrics (Ключи строго как в заголовках таблицы)
    rhr = safe_float(morning.get("Resting_HR"), 60)
    hrv = safe_float(morning.get("HRV"), 45)
    vo2_garmin = safe_float(morning.get("VO2max_Garmin"), 0)
    tsb_raw = morning.get("TSB_Garmin", None)
    tsb_garmin = safe_float(tsb_raw, 999)
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

    recovery_raw = morning.get("Recovery_Time", None)
    recovery_raw_str = str(recovery_raw).replace('\xa0', '').strip()

    recovery_h = int(safe_float(recovery_raw, 0))
    recovery_present = recovery_raw is not None and recovery_raw_str not in ["", "None", "Н/Д"]
        
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
    
    if tsb_garmin != 999:
        tsb = round(tsb_garmin, 1)
    
    # Recovery fallback: если из Morning не пришло значение, считаем сами
    if not recovery_present:
        recovery_h = estimate_recovery_hours(
            acts=activities,
            today_str=today,
            ftp=FTP_GARMIN,
            hrv=hrv,
            rhr=rhr,
            tsb=tsb
        )

        # Защита: если вчера не было тренировки, recovery не должен расти
        try:
            yesterday_acts = [
                a for a in activities
                if a.get("start_date_local", "")[:10] == yesterday_str
                and a.get("type") not in ["Walk", "Hike"]
            ]

            if not yesterday_acts:
                y_row = next(
                    (row for row in reversed(records) if yesterday_str in str(row.get("Date", ""))),
                    None
                )

                if y_row:
                    y_recovery = int(float(y_row.get("Recovery_Time") or 0))
                    if y_recovery > 0 and recovery_h > y_recovery:
                        print(f"DEBUG recovery_h capped by yesterday: {recovery_h} -> {y_recovery}")
                        recovery_h = y_recovery
        except Exception as e:
            print(f"⚠️ Recovery guard error: {e}")

        print(f"DEBUG recovery_h estimated: {recovery_h}")
    
    # --- ВЕЧЕРНИЙ ОТЧЁТ (после 21:30) ---
    if now_dt.hour > 21 or (now_dt.hour == 21 and now_dt.minute >= 30):

        day_acts = [a for a in activities if a.get("start_date_local", "")[:10] == today and a.get("type") not in ["Walk", "Hike"]]

        total_tss = 0
        total_minutes = 0
        details = []

        for a in sorted(day_acts, key=lambda x: x.get("start_date_local", "")):
            a_type = a.get("type")
            t_sec = a.get("moving_time", 0)
            dur_min = round(t_sec / 60, 1)

            tss = 0

            if a_type in ["Ride", "VirtualRide"]:
                w = safe_float(a.get("average_watts"), 0)
                tss = (t_sec/3600)*(w/FTP_GARMIN)**2*100 if w > 0 else 0

            elif a_type in ["Weight Training", "Workout", "WeightTraining", "Gym"]:
                tss = (t_sec / 60) * 0.6

            tss = round(tss, 1)

            total_tss += tss
            total_minutes += dur_min

            name = a.get("name", "Тренировка")
            details.append(f"• {name} — {dur_min} мин | TSS {tss}")

        total_tss = round(total_tss, 1)
        total_minutes = round(total_minutes, 1)

        acts_text = "\n".join(details) if details else "Сегодня без тренировок."

        prompt = (
            f"Ты — АРНИ, стиль: коротко, точно, уверенно. "
            f"Атлет {round(get_bio_age())} лет.\n"

            f"ИТОГИ ДНЯ:\n"
            f"Активностей: {len(day_acts)}\n"
            f"Общее время: {total_minutes} мин\n"
            f"Суммарный TSS: {total_tss}\n"

            f"HRV {int(hrv)}, Пульс {int(rhr)}, Сон {sleep}ч, "
            f"Recovery {recovery_h}ч, TSB {tsb}\n"

            f"\nПРАВИЛА:\n"
            f"- Учитывай суммарную нагрузку, а не одну тренировку\n"
            f"- Если 2+ активности — оцени накопление усталости\n"
            f"- Не преувеличивай и не обесценивай\n"
            f"- Не пиши общих фраз вроде 'сильный' или 'тренируйся', если можно сказать конкретнее\n"

            f"\nОТВЕТ СТРОГО:\n"
            f"1. СТАТУС: Оцени суммарную нагрузку дня.\n"
            f"2. АНАЛИЗ: Что говорят общий TSS, две активности, HRV, пульс, сон и TSB.\n"
            f"3. ЗАВТРА: Четкий режим — отдых / Z1 / Z2, и коротко почему.\n"
        )

        ai_msg = ask_arnie(prompt, "День завершён. Работай по плану.")

        report = (
            f"🌙 ИТОГИ ДНЯ \n\n"
            f"⚡️ FTP: {FTP_GARMIN}\n"
            f"🏋️ Активностей: {len(day_acts)}\n"
            f"⏱ Общее время: {total_minutes} мин\n"
            f"📈 Суммарный TSS: {total_tss}\n\n"
            f"{acts_text}\n\n"
            f"🤖 АРНИ:\n{ai_msg}"
        )

        send_tg(report)
        return

    # Мы берем переменную sleep, которая уже была рассчитана выше в коде
    sleep_hours = sleep

    # Данные для расчета (убедись, что они определены выше)
    hrv_14d_avg = get_hrv_14d_avg_from_sheet(sheet, today) if sheet else 85.0
    print("DEBUG hrv_14d_avg:", hrv_14d_avg)

    # --- 3. ПЕРСОНАЛИЗИРОВАННЫЙ РАСЧЕТ ГОТОВНОСТИ (v2.1) & FitAge
    score = 3.5  # База: учитываем отличный Fitness Age 48

    # HRV: Вариабельность (твой диапазон 30-128)
    if hrv > 95:
        score += 1.0
    elif hrv > 75:
        score += 0.5
    elif 40 <= hrv <= 75:
        # В рабочей зоне смотрим на тренд относительно недели
        if hrv < hrv_14d_avg * 0.8:
            score -= 0.5
        elif hrv > hrv_14d_avg * 1.1:
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
    if recovery_h > 36:
        score -= 0.8
    elif recovery_h > 24:
        score -= 0.5
    elif recovery_h > 12:
        score -= 0.3
    elif recovery_h > 6:
        score -= 0.1

    # Форма (TSB / Acute Load)
    if tsb < -20:
        score -= 1.0 if hrv < 60 else 0.5
    elif -10 <= tsb <= 5:
        score += 0.3

    # Финальное ограничение диапазона [0...5]
    score = max(0.0, min(5.0, round(score, 1)))

    # --- STATUS + RECOVERY TEXT ---
    if score >= 4:
        day_status = "🟢 Готов"
    elif score >= 2.8:
        day_status = "🟡 Осторожно"
    else:
        day_status = "🔴 Восстановление"

    if recovery_h >= 24:
        rec_text = f"{recovery_h}ч 🔴 (нужно восстановление)"
    elif recovery_h >= 12:
        rec_text = f"{recovery_h}ч 🟡 (есть усталость)"
    elif recovery_h >= 6:
        rec_text = f"{recovery_h}ч 🟡 (лёгкая усталость)"
    else:
        rec_text = f"{recovery_h}ч 🟢 (свежесть)"
    
    # --- 1. БАЗОВАЯ ФОРМА (Долгосрочная) ---
    ##vo2_calc = vo2_val if vo2_val else 32.7
    vo2_calc = vo2_garmin if vo2_garmin > 0 else (vo2_val if vo2_val else 32.7)
    vo2_source = "Garmin" if vo2_garmin > 0 else "Strava"

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

    ##if eftp_val:
        ##update_eftp_in_sheet(today, eftp_val)

    # 6. --- ПРОМПТ И ОТЧЕТ (VERBATIM GITHUB) ---# Report

    if score >= 4.8:
        status_icon = "🔥🏆"
    elif score >= 4.0:
        status_icon = "🟢🟢"
    elif score >= 2.8:
        status_icon = "🟡"
    else:
        status_icon = "🔴"
        
    if not today_acts:
        
        if sleep < 5.5 and score < 4.0:
            sleep_note = "- ВАЖНО: Сон очень короткий и готовность неидеальна. Сегодня только лёгкая работа, без Зоны 3.\n"
        elif sleep < 5.5 and score >= 4.0:
            sleep_note = "- Сон короткий, но метрики сильные. Допустима умеренная работа в Z2, короткие включения выше — только без жёсткого объёма.\n"
        elif sleep < 6.5:
            sleep_note = "- Сон немного ограничен. Работай в Z2, Z3 — умеренно и без агрессии.\n"
        else:
            sleep_note = ""

        prompt = (
             f"Ты — АРНИ, стиль: жесткий, лаконичный, уверенный тренер. "
             f"Без хамства, без крика, без панибратства. "
             f"НЕ начинай с фраз типа: 'Слушай сюда', 'Чемпион', 'Боец'. "
             f"Атлет: {round(get_bio_age())} лет. "

             f"ДАННЫЕ: HRV {int(hrv)}, Пульс {int(rhr)}, Сон {sleep}ч (Глубокий: {deep_sleep}ч), "
             f"Sleep Score: {sleep_score}, Recovery: {recovery_h}ч, TSB {tsb}, Готовность {score}/5. "
             f"Fit Age {f_age}. VO2max: {vo2_calc}. "

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

        if eftp_val:
            delta = eftp_val - FTP_GARMIN

            if delta <= -15:
                eftp_icon = "🔴"
            elif delta <= -7:
                eftp_icon = "🟡"
            elif delta <= 5:
                eftp_icon = "🟢"
            else:
                eftp_icon = "🚀"
       
            ftp_line = f"🚴 FTP: {FTP_GARMIN} | ⚡ {eftp_val} ({delta:+}) {eftp_icon}"
        else:
            ftp_line = f"🚴 FTP: {FTP_GARMIN}"

        # 2. И только потом используем его в отчете
 
        report = (f"🌅 УТРЕННИЙ СТАТУС {status_icon}\n\n"
                  f"{ftp_line}\n"
                  f"❤️ Пульс: {int(rhr)} | 🌀 HRV: {int(hrv)}\n"
                  f"🛡 Статус: {day_status}\n"
                  f"🔋 Готовность: {score}/5\n"
                  f"🕒 Восстановление: {rec_text}\n"
                  f"😴 Качество сна: {sleep_score} ({s_status})\n"
                  f"📊 Форма (TSB): {tsb} | VO2max: {vo2_calc} ({vo2_source})\n"
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
        w_avg = safe_float(last.get("average_watts"), 0)
        hr_avg = safe_float(last.get("average_heartrate"), 0)
        hr_max_act = safe_float(last.get("max_heartrate"), 0)
        
        # Расчет TSS
        if a_type_last in ["Ride", "VirtualRide"]:
            tss_last = round((t_sec/3600)*(w_avg/FTP_GARMIN)**2*100, 1) if w_avg else 0
        elif a_type_last in ["Weight Training", "Workout", "WeightTraining", "Gym"]:
            tss_last = round((t_sec / 60) * 0.6, 1)
        else:
            tss_last = 0

        # Формируем умный промпт для анализа нагрузки
        prompt = (
            f"Ты — АРНИ, стиль: коротко, точно, уважительно, без хамства. "
            f"Разбери тренировку атлета {round(get_bio_age())} лет: {name}. "

            f"ДАННЫЕ: Тип {a_type_last}. Длительность {dur_min} мин. Дистанция {dist} км. "
            f"TSS {tss_last}. Средняя мощность {w_avg}. "
            f"Пульс ср/макс: {hr_avg}/{hr_max_act}. "
            f"Утренняя готовность была {score}/5. "

            f"\nПРАВИЛА АНАЛИЗА:\n"
            f"- Не делай вывод только по TSS.\n"
            f"- Если это Ride или VirtualRide и средняя мощность заметная, а пульс высокий, не называй работу прогулкой.\n"
            f"- Если это силовая тренировка длительностью {dur_min} мин, оценивай её как рабочий стимул, а не как отдых.\n"
            f"- Признавай усилие, если тренировка реально была рабочей.\n"
            f"- Будь строгим в дисциплине, но без унижения и обесценивания.\n"
            f"- Каждый пункт максимум 2 коротких предложения.\n"

            f"\nОТВЕТЬ СТРОГО ПО ПУНКТАМ:\n"
            f"1. СТАТУС: Реальная интенсивность нагрузки.\n"
            f"2. ФИДБЕК: Что было сделано хорошо.\n"
            f"3. ЗАВТРА: Конкретный совет по восстановлению.\n"

            f"Без вступлений. Без фраз типа 'Слушай сюда'. "
            f"В конце — одна короткая фраза Арнольда на русском."
        )
        ai_msg = ask_arnie(prompt, "Тренировка зафиксирована. Анализ будет позже.")

        report = (f"🏃 ТРЕНИРОВКА {status_icon} \n\n"
                  f"<b>{html.escape(name)}</b>\n"
                  f"📍 {dist} км | ⏱ {dur_min} мин \n"
                  f" 📈 TSS: {tss_last}\n\n"
                  f"🤖 АРНИ:\n{ai_msg}")
    send_tg(report)

if __name__ == "__main__":
    main()
