import os, requests, json, sys, gspread, html, time
from datetime import datetime, timedelta
from google.oauth2.service_account import Credentials

# --- CONFIG ---
BIRTH_DATE = datetime(1963, 5, 29)
FTP_GARMIN = 227 
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
def safe_float(val, default=0.0, allow_negative=False):
    if val is None:
        return default
    s_val = str(val).replace(',', '.').replace('\xa0', '').strip()
    if s_val in ["", "Н/Д", "None"]:
        return default
    try:
        v = float(s_val)
        if allow_negative:
            return v
        return v if v >= 0 else default
    except:
        return default

def power_is_trusted(a):
    w = safe_float(a.get("average_watts"), 0)
    if w <= 0:
        return False

    name = str(a.get("name", "")).lower()
    sport_type = str(a.get("sport_type", "")).lower()

    return (
        a.get("type") == "VirtualRide"
        or sport_type == "virtualride"
        or bool(a.get("device_watts"))
        or bool(a.get("trainer"))
        or "зальный велоспорт" in name
        or "indoor" in name
        or "virtual" in name
        or "technogym" in name
    )

def eftp_is_eligible(a):
    if not power_is_trusted(a):
        return False

    t_sec = a.get("moving_time", 0) or 0
    w = safe_float(a.get("average_watts"), 0)
    hr = safe_float(a.get("average_heartrate"), 0)

    if t_sec < 20 * 60:   # меньше 20 минут не берём для eFTP
        return False
    if w < 120:
        return False
    if hr < 115:
        return False

    return True

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

def get_7d_load_from_activities(activities, today_str):
    """
    TSS за последние 7 календарных дней до сегодня.
    Сегодня исключаем, чтобы утром не мешать текущую активность с базовой формой.
    """
    vals_by_day = {}

    try:
        today_dt = datetime.strptime(today_str, "%Y-%m-%d").date()
        start_dt = today_dt - timedelta(days=7)
    except Exception:
        return 0, 0

    for a in activities:
        date_str = a.get("start_date_local", "")[:10]

        try:
            act_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except Exception:
            continue

        if not (start_dt <= act_date < today_dt):
            continue

        t_sec = a.get("moving_time", 0) or 0
        if t_sec <= 0:
            continue

        tss = calc_activity_tss(a, FTP_GARMIN)

        if tss > 0:
            vals_by_day[date_str] = vals_by_day.get(date_str, 0) + tss

    total7 = round(sum(vals_by_day.values()), 1)
    avg7 = round(total7 / 7, 1)

    return avg7, total7

def get_eftp_trend_from_sheet(sheet, today_str, current_eftp, lookback=7):
    try:
        if not sheet or not current_eftp:
            return "", None

        all_values = sheet.get_all_values()
        if not all_values or len(all_values) < 2:
            return "", None

        header = [str(h).replace('\xa0', '').strip() for h in all_values[0]]

        if "Date" not in header or "eFTP_Strava" not in header:
            return "", None

        date_idx = header.index("Date")
        eftp_idx = header.index("eFTP_Strava")

        vals = []
        for row in reversed(all_values[1:]):
            if len(row) <= max(date_idx, eftp_idx):
                continue

            row_date = str(row[date_idx]).strip()
            if row_date.startswith(today_str):
                continue

            v = safe_float(row[eftp_idx], 0)
            if v > 0:
                vals.append(v)

            if len(vals) >= lookback:
                break

        if len(vals) < 3:
            return "", None

        prev_avg = round(sum(vals) / len(vals), 1)
        delta = round(current_eftp - prev_avg, 1)

        if delta >= 3:
            arrow = "↑"
        elif delta <= -3:
            arrow = "↓"
        else:
            arrow = "→"

        return f" {arrow}{delta:+.1f}/7д", prev_avg

    except Exception as e:
        print(f"⚠️ eFTP trend error: {e}")
        return "", None

def detect_anomalies(hrv, rhr, tss_today, eftp_val, ftp):
    flags = []

    if hrv > 130 or hrv < 25:
        flags.append("HRV аномалия")

    if rhr > 65 or rhr < 38:
        flags.append("RHR вне диапазона")

    if tss_today > 250:
        flags.append("очень высокий TSS")

    if eftp_val and abs(eftp_val - ftp) > 40:
        flags.append("eFTP скачок")

    return flags

def calc_activity_tss(a, ftp):
    a_type = a.get("type")
    t_sec = a.get("moving_time", 0) or 0

    if t_sec <= 0:
        return 0.0

    if a_type in ["Ride", "VirtualRide"]:
        w = safe_float(a.get("average_watts"), 0)
        hr_a = safe_float(a.get("average_heartrate"), 0)

        if power_is_trusted(a) and w > 0 and ftp > 0:
            return round((t_sec / 3600) * (w / ftp) ** 2 * 100, 1)

        if hr_a > 0:
            base = (t_sec / 60) * 0.35

            if hr_a >= 135:
                base *= 1.15
            elif hr_a < 110:
                base *= 0.9

            return round(base, 1)

        return 0.0

    if a_type in ["Weight Training", "Workout", "WeightTraining", "Gym"]:
        hr_a = safe_float(a.get("average_heartrate"), 0)
        base = (t_sec / 60) * 0.45

        if hr_a >= 110:
            base *= 1.15
        elif hr_a < 95:
            base *= 0.9

        return round(base, 1)

    return 0.0

def get_accumulated_fatigue_flag(hrv, hrv_14d_avg, tss7_sum, recovery_h, tsb):
    reasons = []

    if hrv_14d_avg > 0 and hrv < hrv_14d_avg * 0.85:
        reasons.append("HRV ниже тренда")

    if tss7_sum >= 180:
        reasons.append("высокая 7д нагрузка")

    if recovery_h >= 12:
        reasons.append("остаток восстановления")

    if tsb < -10:
        reasons.append("TSB в заметном минусе")

    if len(reasons) >= 2:
        return "⚠️ Накопленная усталость: " + ", ".join(reasons)

    return ""

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
        res_m = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}",
            timeout=10
        )
        res_m.raise_for_status()

        models_data = res_m.json()
        available = []

        for m in models_data.get("models", []):
            name = m.get("name", "")
            methods = m.get("supportedGenerationMethods", [])

            if "generateContent" not in methods:
                continue

            bad_markers = [
                "tts",
                "image",
                "audio",
                "lyria",
                "robotics",
                "computer-use",
                "deep-research",
                "nano-banana",
            ]

            if any(marker in name for marker in bad_markers):
                continue

            available.append(name)

        #print("DEBUG Gemini available models:", available)

        if not available:
            print("⚠️ Gemini: no available models")
            return fallback_text

        preferred_order = [
            "models/gemini-2.5-flash",
            "models/gemini-2.0-flash",
            "models/gemini-2.5-flash-lite",
            "models/gemini-2.0-flash-001",
            "models/gemini-flash-latest",
            "models/gemini-flash-lite-latest",
            "models/gemini-2.5-pro",
            "models/gemini-pro-latest",
        ]

        model_queue = []

        for p in preferred_order:
            if p in available and p not in model_queue:
                model_queue.append(p)

        for m in available:
            if m not in model_queue:
                model_queue.append(m)

        ##print("DEBUG Gemini model queue:", model_queue)

        last_error = None

        for model_name in model_queue[:4]:
            url = f"https://generativelanguage.googleapis.com/v1beta/{model_name}:generateContent?key={GEMINI_API_KEY}"

            for attempt in range(3):
                try:
                    res_ai = requests.post(
                        url,
                        json={"contents": [{"parts": [{"text": prompt}]}]},
                        timeout=30
                    )

                    ##print(f"DEBUG Gemini model {model_name}, attempt {attempt + 1}: status {res_ai.status_code}")

                    try:
                        data = res_ai.json()
                    except Exception:
                        data = {"raw_text": res_ai.text}

                    if "candidates" in data and data["candidates"]:
                        try:
                            text = data["candidates"][0]["content"]["parts"][0]["text"]
                            return html.escape(
                                text.strip().replace("_", " ").replace("*", " ")
                            )
                        except Exception as e:
                            last_error = f"model={model_name}, bad candidate format: {e}"
                            print(f"⚠️ Gemini parse error: {last_error}")
                            break

                    err = data.get("error", {})
                    code = err.get("code")
                    status = err.get("status", "")
                    message = err.get("message", "")

                    if not err:
                        message = str(data)[:500]

                    last_error = f"model={model_name}, code={code}, status={status}, message={message}"
                    print(f"⚠️ Gemini failed: {last_error}")

                    if code in [429, 500, 503] or status in ["UNAVAILABLE", "RESOURCE_EXHAUSTED", "INTERNAL"]:
                        if attempt < 1:
                            time.sleep(3 * (attempt + 1))
                            continue

                    break

                except Exception as e:
                    last_error = f"model={model_name}, exception={e}"
                    print(f"⚠️ Gemini exception: {last_error}")
                    if attempt < 1:
                        time.sleep(3 * (attempt + 1))
                        continue
                    break

        print(f"⚠️ Gemini fallback used. Last error: {last_error}")
        return fallback_text

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
            if str(val).startswith(target_date):
                header = sheet.row_values(1)
                header = [h.replace('\xa0', '').strip() for h in header]

                target_column = "eFTP_Strava"

                if target_column in header:
                    sheet.update_cell(i + 1, header.index(target_column) + 1, eftp_val)
                    print(f"✅ eFTP_Strava {eftp_val} записан.")
                    break
    except Exception as e:
        print(f"⚠️ Sheet update error: {e}")

def update_age_in_sheet(target_date, age_val):
    try:
        client = get_google_client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        dates = sheet.col_values(1)

        for i, val in enumerate(dates):
            if str(val).startswith(target_date):
                header = sheet.row_values(1)
                header = [str(h).replace('\xa0', '').strip() for h in header]

                possible_columns = ["Age"]
                target_idx = None

                for col in possible_columns:
                    if col in header:
                        target_idx = header.index(col) + 1
                        break

                if target_idx:
                    sheet.update_cell(i + 1, target_idx, round(age_val, 1))
                    print(f"✅ Age {round(age_val, 1)} записан.")
                else:
                    print(f"⚠️ Колонка Age не найдена. Header: {header}")
                break
    except Exception as e:
        print(f"⚠️ Age update error: {e}")

def update_tsb_strava_in_sheet(target_date, tsb_val):
    try:
        client = get_google_client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        dates = sheet.col_values(1)

        for i, val in enumerate(dates):
            if str(val).startswith(target_date):
                header = sheet.row_values(1)
                header = [h.replace('\xa0', '').strip() for h in header]

                target_column = "TSB_Strava"

                if target_column in header:
                    sheet.update_cell(i + 1, header.index(target_column) + 1, tsb_val)
                    print(f"✅ TSB_Strava {tsb_val} записан.")
                    break
    except Exception as e:
        print(f"⚠️ TSB_Strava update error: {e}")

def update_recovery_in_sheet(target_date, recovery_val):
    try:
        client = get_google_client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        dates = sheet.col_values(1)

        for i, val in enumerate(dates):
            if str(val).startswith(target_date):
                header = sheet.row_values(1)
                header = [h.replace('\xa0', '').strip() for h in header]

                target_column = "Recovery_Time"

                if target_column in header:
                    sheet.update_cell(i + 1, header.index(target_column) + 1, recovery_val)
                    print(f"✅ Recovery_Time {recovery_val} записан.")
                    break
    except Exception as e:
        print(f"⚠️ Recovery_Time update error: {e}")

def update_fit_age_in_sheet(target_date, fit_age_val):
    try:
        client = get_google_client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        dates = sheet.col_values(1)

        for i, val in enumerate(dates):
            if str(val).startswith(target_date):
                header = sheet.row_values(1)
                header = [str(h).replace('\xa0', '').strip() for h in header]

                possible_columns = ["FitAge", "Fitness_Age", "Fit Age", "Fitness Age"]
                target_idx = None

                for col in possible_columns:
                    if col in header:
                        target_idx = header.index(col) + 1
                        break

                if target_idx:
                    sheet.update_cell(i + 1, target_idx, fit_age_val)
                    print(f"✅ Fit Age {fit_age_val} записан.")
                else:
                    print(f"⚠️ Колонка для Fit Age не найдена. Header: {header}")
                break
    except Exception as e:
        print(f"⚠️ Fit Age update error: {e}")

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

def get_morning_datetime_from_sheet(target_date):
    try:
        client = get_google_client()
        sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Morning")
        all_values = sheet.get_all_values()

        if not all_values or len(all_values) < 2:
            return None

        header = [str(h).replace('\xa0', '').replace("'", "").strip() for h in all_values[0]]

        if "Date" not in header:
            print("⚠️ В таблице не найдена колонка Date")
            return None

        date_idx = header.index("Date")

        for row in reversed(all_values[1:]):
            if len(row) <= date_idx:
                continue

            row_date = str(row[date_idx]).replace("'", "").strip()

            if row_date.startswith(target_date):
                try:
                    return datetime.strptime(row_date[:16], "%Y-%m-%d %H:%M")
                except Exception:
                    try:
                        return datetime.strptime(row_date[:19], "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        return None

    except Exception as e:
        print(f"⚠️ Morning datetime read error: {e}")

    return None

def get_last_activity_end_yesterday(acts, yesterday_str):
    last_end = None

    for a in acts:
        if a.get("start_date_local", "")[:10] != yesterday_str:
            continue
        if a.get("type") in ["Walk", "Hike"]:
            continue

        start_str = a.get("start_date_local", "")
        dur_sec = a.get("moving_time", 0) or 0

        if not start_str or dur_sec <= 0:
            continue

        try:
            start_dt = datetime.strptime(start_str[:19], "%Y-%m-%dT%H:%M:%S")
        except Exception:
            try:
                start_dt = datetime.strptime(start_str[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue

        end_dt = start_dt + timedelta(seconds=dur_sec)

        if last_end is None or end_dt > last_end:
            last_end = end_dt

    return last_end

def estimate_performance(activities, weight):
    vals_vo2 = []
    hr_max = 208 - (0.7 * get_bio_age())

    if not weight or weight <= 0:
        weight = 88.0

    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        if not eftp_is_eligible(a):
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

    yesterday_wake_dt = get_morning_datetime_from_sheet(yesterday_str)
    today_wake_dt = get_morning_datetime_from_sheet(today_str)
    last_end_dt = get_last_activity_end_yesterday(acts, yesterday_str)

    yesterday_acts = [
        a for a in acts
        if a.get("start_date_local", "")[:10] == yesterday_str
        and a.get("type") not in ["Walk", "Hike"]
    ]

    base_rec = 0.0

    for a in yesterday_acts:
        a_type = a.get("type")
        t_sec = a.get("moving_time", 0) or 0

        if t_sec <= 0:
            continue

        if a_type in ["Ride", "VirtualRide"]:
            w_avg = safe_float(a.get("average_watts"), 0)
            hr_a = safe_float(a.get("average_heartrate"), 0)
            tss_last = calc_activity_tss(a, ftp)

            if power_is_trusted(a) and w_avg > 0 and ftp > 0 and tss_last > 0:
                rec_add = tss_last * 0.30
            elif hr_a > 0 and tss_last > 0:
                rec_add = tss_last * 0.22
            elif hr_a > 0:
                rec_add = (t_sec / 60) * 0.18
            else:
                rec_add = (t_sec / 60) * 0.12

        elif a_type in ["Weight Training", "Workout", "WeightTraining", "Gym"]:
            rec_add = (t_sec / 60) * 0.08

        else:
            rec_add = (t_sec / 60) * 0.20

        base_rec += rec_add

    # --- ВЕТКА 1: продолжаем вчерашний recovery по реальному времени ---
    if y_recovery is not None and y_recovery > 0 and yesterday_wake_dt and today_wake_dt and last_end_dt:
        carryover_to_last_end = max(
            0,
            y_recovery - ((last_end_dt - yesterday_wake_dt).total_seconds() / 3600)
        )

        recovery_h = carryover_to_last_end + base_rec - (
            (today_wake_dt - last_end_dt).total_seconds() / 3600
        )

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

        return max(0, min(72, int(recovery_h)))

    # --- ВЕТКА 2: если вчера тренировок не было, догорает только старый recovery ---
    if not yesterday_acts:
        if y_recovery is not None and y_recovery > 0 and yesterday_wake_dt and today_wake_dt:
            recovery_h = y_recovery - (
                (today_wake_dt - yesterday_wake_dt).total_seconds() / 3600
            )

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

            return max(0, min(72, int(recovery_h)))

        return 0
        
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
                print(f"⚠️ Morning: строки за сегодня {today} нет. Использую пустые/default значения, а не старую строку.")
                morning = {}
        
    except Exception as e:
        print(f"❌ Sheets fail: {e}")
        
    core_morning_keys = ["Resting_HR", "HRV", "Sleep_Score", "Sleep_Hours", "Recovery_Time"]

    morning_is_fresh = any(
        str(morning.get(k, "")).replace('\xa0', '').strip() not in ["", "None", "Н/Д"]
        for k in core_morning_keys
    )    

    # Metrics (Ключи строго как в заголовках таблицы)
    rhr = safe_float(morning.get("Resting_HR"), 60)
    hrv = safe_float(morning.get("HRV"), 45)
    vo2_garmin = safe_float(morning.get("VO2max_Garmin"), 0)
    tsb_raw = morning.get("TSB_Garmin", None)
    tsb_garmin = safe_float(tsb_raw, 999, allow_negative=True)
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
    strength_feel = int(safe_float(morning.get("Strength_Feel"), 0))
    strength_effort = int(safe_float(morning.get("Strength_Effort"), 0))
    ride_feel = int(safe_float(morning.get("Ride_Feel"), 0))
    ride_effort = int(safe_float(morning.get("Ride_Effort"), 0))

    recovery_raw = morning.get("Recovery_Time", None)
    recovery_raw_str = str(recovery_raw).replace('\xa0', '').strip()

    recovery_h = int(safe_float(recovery_raw, 0))
    recovery_present = recovery_raw is not None and recovery_raw_str not in ["", "None", "Н/Д"]
        
    # Расчет производительности (обязательно!)
    vo2_val, eftp_calc_val = estimate_performance(activities, weight=weight)
    eftp_val = int(round(eftp_calc_val)) if eftp_calc_val is not None and eftp_calc_val > 0 else None
    
    # Оставляем только один расчет today_acts здесь
    today_acts = [a for a in activities if a.get("start_date_local", "")[:10] == today and a.get("type") not in ["Walk", "Hike"]]
    tss7_avg, tss7_sum = get_7d_load_from_activities(activities, today)

    ctl, atl = 0, 0

    # 2. Цикл накопления (проходим по всей истории)
    for a in sorted(activities, key=lambda x: x.get("start_date_local", "")):
        # ИСКЛЮЧАЕМ тренировки за сегодня для фиксации утренней формы
        if a.get("start_date_local", "")[:10] == today:
            continue
            
        tss = calc_activity_tss(a, FTP_GARMIN)
        
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
    tsb_strava = tsb
    
    if tsb_garmin != 999:
        tsb = round(tsb_garmin, 1)

    update_tsb_strava_in_sheet(today, tsb_strava)

    if eftp_val is not None and eftp_val > 0:
        update_eftp_in_sheet(today, eftp_val)

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
                        recovery_h = y_recovery
        except Exception as e:
            print(f"⚠️ Recovery guard error: {e}")

        # Финальная развязка: если утро явно свежее, мелкий остаток recovery обнуляем
        if (
            0 < recovery_h <= 8
            and rhr <= 48
            and hrv >= 60
            and tsb >= 5
            and sleep_score >= 80
        ):
            recovery_h = 0

        update_recovery_in_sheet(today, recovery_h)

    eftp_trend_text = ""
    if sheet and eftp_val is not None and eftp_val > 0:
        eftp_trend_text, _ = get_eftp_trend_from_sheet(sheet, today, eftp_val)

    if eftp_val is not None and eftp_val > 0:
        delta = int(round(eftp_val - FTP_GARMIN))

        if delta <= -15:
            eftp_icon = "🔴"
        elif delta <= -7:
            eftp_icon = "🟡"
        elif delta <= 5:
            eftp_icon = "🟢"
        else:
            eftp_icon = "🚀"

        ftp_line = f"🚴 FTP/eFTP: {FTP_GARMIN}/{int(round(eftp_val))} ({delta:+}) {eftp_icon}{eftp_trend_text}"
    else:
        ftp_line = f"🚴 FTP: {FTP_GARMIN}"
        
    # --- ВЕЧЕРНИЙ ОТЧЁТ (после 20:30 UTC+2) ---
    if now_dt.hour > 18 or (now_dt.hour == 18 and now_dt.minute >= 30):

        all_day_acts = [a for a in activities if a.get("start_date_local", "")[:10] == today]
        day_acts = [a for a in all_day_acts if a.get("type") not in ["Walk", "Hike"]]

        total_tss = 0
        total_minutes = 0
        details = []

        for a in sorted(all_day_acts, key=lambda x: x.get("start_date_local", "")):
            a_type = a.get("type")
            t_sec = a.get("moving_time", 0)
            dur_min = round(t_sec / 60, 1)

            include_in_stats = a_type not in ["Walk", "Hike"]
            tss = calc_activity_tss(a, FTP_GARMIN)

            if include_in_stats:
                total_tss += tss
                total_minutes += dur_min

            name = a.get("name", "Тренировка")
            dist_km = round((a.get("distance", 0) or 0) / 1000, 2)
            steps = int(a.get("steps") or 0)

            if include_in_stats:
                if a_type in ["Ride", "VirtualRide"] and dist_km > 0:
                    details.append(f"• {name} — {dist_km} км | {dur_min} мин | TSS {tss:.1f}")
                else:
                    details.append(f"• {name} — {dur_min} мин | TSS {tss:.1f}")
            else:
                if dist_km > 0 and steps > 0:
                    details.append(f"• {name} — {dist_km} км | {dur_min} мин | {steps} шагов")
                elif dist_km > 0:
                    details.append(f"• {name} — {dist_km} км | {dur_min} мин")
                else:
                    details.append(f"• {name} — {dur_min} мин")

        total_tss = round(total_tss, 1)
        total_minutes = round(total_minutes, 1)

        flags = detect_anomalies(hrv, rhr, total_tss, eftp_val, FTP_GARMIN)
        flags_text = f"⚠️ {' | '.join(flags)}\n\n" if flags else ""

        acts_text = "\n".join(details) if details else "Сегодня без тренировок."
        
        #---END OF DAY REPORT
        prompt = (
            f"Ты — АРНИ, стиль: коротко, точно, уверенно. "
            f"Атлет {round(get_bio_age(), 1)} лет.\n"

            f"ИТОГИ ДНЯ\n\n"
            f"Тренировок: {len(day_acts)}\n"
            f"Всего активностей: {len(all_day_acts)}\n"
            f"Общее время: {total_minutes} мин\n"
            f"Суммарный TSS: {total_tss}\n"
            f"Нагрузка 7д: {tss7_sum} (ср {tss7_avg})\n"
            f"\nАКТИВНОСТИ ДНЯ:\n{acts_text}\n"

            f"HRV {int(hrv)}, Пульс {int(rhr)}, Сон {sleep}ч, "
            f"Recovery {recovery_h}ч, TSB {tsb}, "
            f"Strength_Feel {strength_feel}, Strength_Effort {strength_effort}, "
            f"Ride_Feel {ride_feel}, Ride_Effort {ride_effort}\n"

            f"\nПРАВИЛА:\n"
            f"- Учитывай суммарную нагрузку, а не одну тренировку\n"
            f"- Если 2+ активности — оцени накопление усталости\n"
            f"- Не преувеличивай и не обесценивай\n"
            f"- Не называй TSS около 35-60 разминкой, минимальной нагрузкой или пустяком. Это лёгкая или умеренная, но полноценная работа.\n"
            f"- Одна велотренировка 30-45 минут с TSS около 40-50 — это не тяжёлая сессия, но и не нулевая нагрузка. Называй её рабочей, поддерживающей или умеренной.\n"
            f"- Если Ride Feel 4 и Ride Effort 3, трактуй это как хорошую рабочую поездку, перенесённую уверенно, а не как разминку.\n"
            f"- Не пиши общих фраз вроде 'сильный' или 'тренируйся', если можно сказать конкретнее\n"

            f"- После длительной поездки 150+ минут не пиши, что ноги должны быть свежими.\n"
            f"- Корректная логика: если субъективно сохранилась лёгкость, возможна восстановительная Z1 или очень лёгкая Z2; если есть тяжесть в ногах — только восстановление.\n"
            f"- После такой сессии не обещай автоматическую свежесть на следующий день, даже если Ride Feel высокий и Effort низкий.\n"

            f"- Для одной велосессии длительностью 100-130 минут с TSS около 30-45 используй формулировки: хороший объём, длинная базовая аэробная работа, заметная, но не тяжёлая нагрузка.\n"
            f"- Не называй такую сессию тяжёлой или серьёзно стрессовой, если Ride Effort <= 2 и субъективно она перенесена легко.\n"
            f"- После такой поездки базовый вывод на завтра: Z1 или очень лёгкая Z2 по ощущениям в ногах, а не только Z1.\n"
            f"- Если недельная нагрузка 300+ TSS и есть fatigue flag, не пиши 'нагрузка сбалансирована'. Пиши: нагрузка высокая, но пока контролируемая.\n"

            f"- Если за день была одна поездка длительностью 150+ минут, не пиши слишком коротко и сухо.\n"
            f"- В таком случае каждый из пунктов 1, 2 и 3 должен содержать минимум 2 полных предложения.\n"
            f"- Не используй пустые формулировки вроде: 'Отличная работа', 'Всё под контролем', 'Тело готово', если их можно заменить конкретикой.\n"
            f"- Для длинной поездки 150+ минут с TSS примерно 55-80 используй формулировки: серьёзный аэробный объём, большая базовая работа, заметная, но контролируемая нагрузка.\n"
            f"- Если Ride Feel >= 4 и Ride Effort <= 2, обязательно подчёркивай, что большой объём был перенесён легко и экономично.\n"
            f"- В пункте АНАЛИЗ обязательно связывай объём поездки с её переносимостью: большой объём, но без признаков перегруза.\n"
            f"- В пункте ЗАВТРА после поездки 150+ минут не пиши в стиле 'продолжай строить базу' или 'тело готово'.\n"
            f"- После такой сессии базовый вывод на завтра: восстановительная Z1 или очень лёгкая Z2, только если ноги свежие.\n"
            f"- Если длительная поездка была холмистой или с набором высоты, отдельно подчеркни это как дополнительный фактор нагрузки.\n"
            f"- Не называй такую сессию просто рабочей. Это уже полноценная объёмная аэробная работа.\n"
            
            f"- Если указаны Strength_Feel / Strength_Effort, учитывай их как субъективную оценку силовой.\n"
            f"- Если указаны Ride_Feel / Ride_Effort, учитывай их как субъективную оценку вело.\n"

            f"- Не называй короткий outdoor Ride с нормальным рабочим пульсом и Ride Effort 3 минимальной или пустой активностью, даже если TSS низкий.\n"

            f"- Feel: 1=Very Weak, 2=Weak, 3=Normal, 4=Strong, 5=Very Strong.\n"
            f"- Effort по шкале Garmin: 1=очень легко, 2=легко, 3=умеренно, 4=довольно тяжело, 5-6=трудно, 7-8=очень трудно, 9=очень тяжело, 10=максимум.\n"
            f"- Не называй Effort 4-5 экстремальной нагрузкой. Это рабочая, но не предельная тяжесть.\n"
            f"- Effort 7-8 = реально тяжёлая работа. Effort 9-10 = предельная работа.\n"
            f"- Если суммарный TSS за день умеренный (примерно до 45-50), HRV в норме, Recovery низкий, но сон короткий, не пиши 'обязательно отдых'. Корректно: приоритет сон, а на завтра Z1 или очень лёгкая Z2; полный отдых только если есть субъективная тяжесть.\n"
            f"- ШАБЛОНЫ СИЛОВЫХ ДНЕЙ:\n"
            f"  Chest and abs = 4 упражнения на грудь, каждое 4x10, плюс пресс 2x30.\n"
            f"  Back and abs = 4 упражнения на спину, каждое 4x10, плюс пресс 2x30.\n"
            f"  Legs and abs = 4 упражнения на ноги, каждое 4x10, плюс пресс 2x30.\n"
            f"  Arms and abs = 3 упражнения на бицепс и 3 упражнения на трицепс, все по 4x10, плюс пресс 2x30.\n"
            f"  Shoulders, calves and abs = 4 упражнения на плечи по 4x10, 3 упражнения на икры по 4x15, плюс пресс 2x30.\n"
            f"- Не смешивай тяжёлую силовую и лёгкое вело в одну общую субъективную оценку, если оценки различаются.\n"

            f"- Если за день была одна аэробная велосессия длительностью около 50-75 минут с TSS примерно 20-30, не назначай полный отдых как базовый вариант.\n"
            f"- При такой нагрузке и нормальных утренних метриках базовый вывод на завтра — Z1 или лёгкая Z2.\n"
            f"- Полный отдых предлагай только если одновременно есть признаки усталости: низкий HRV, плохой сон, высокий Recovery Time, тяжёлое субъективное состояние или реально высокая суммарная нагрузка за день.\n"
            f"- Если по пульсу основная часть работы прошла в Z2, а средний пульс умеренный, трактуй это как контролируемую базовую аэробную работу, а не как нагрузку, требующую дня полного отдыха.\n"

            f"- Для длительной поездки с большим объёмом не пиши слишком коротко и сухо вроде: 'Рабочий объём. Под полным контролем.'\n"
            f"- Если за день была одна длинная поездка на 150+ минут, в пункте СТАТУС обязательно подчеркни масштаб работы: большая аэробная сессия, серьёзный объём, длинная базовая нагрузка.\n"
            f"- В таком случае пункт АНАЛИЗ должен прямо говорить, что объём был существенным, даже если субъективно сессия далась легко.\n"
            
            f"\nОТВЕТ СТРОГО:\n"
            f"1. СТАТУС: Оцени суммарную нагрузку дня.\n"
            f"2. АНАЛИЗ: Что говорят общий TSS, число тренировок за день, HRV, пульс, сон и TSB.\n"
            f"3. ЗАВТРА: Четкий режим — отдых / Z1 / Z2, и коротко почему. После длительной поездки 150+ минут не предлагай рабочую тренировку как базовый вариант.\n"
        )

        ai_msg = ask_arnie(prompt, "День завершён. Работай по плану.")

        report = (
            f"🌙 ИТОГИ ДНЯ \n\n"
            f"{ftp_line}\n"
            f"{flags_text}"
            f"🏋️ Тренировок: {len(day_acts)}\n"
            f"🚶 Всего активностей: {len(all_day_acts)}\n"
            f"⏱ Общее время: {total_minutes} мин\n"
            f"📈 Суммарный TSS: {total_tss}\n"
            f"📦 Нагрузка 7д: {tss7_sum} TSS | ср {tss7_avg}/день\n"
            f"🏋️ Силовая: Feel {strength_feel if strength_feel > 0 else 'н/д'} | Effort {strength_effort if strength_effort > 0 else 'н/д'}\n"
            f"🚴 Вело: Feel {ride_feel if ride_feel > 0 else 'н/д'} | Effort {ride_effort if ride_effort > 0 else 'н/д'}\n\n"
            f"{acts_text}\n\n"
            f"🤖 АРНИ:\n{ai_msg}"
        )

        send_tg(report)
        return

    # Мы берем переменную sleep, которая уже была рассчитана выше в коде
    sleep_hours = sleep

    # Данные для расчета (убедись, что они определены выше)
    hrv_14d_avg = get_hrv_14d_avg_from_sheet(sheet, today) if sheet else 85.0
    
    fatigue_flag = get_accumulated_fatigue_flag(
        hrv=hrv,
        hrv_14d_avg=hrv_14d_avg,
        tss7_sum=tss7_sum,
        recovery_h=recovery_h,
        tsb=tsb
    )
    ##print("DEBUG hrv_14d_avg:", hrv_14d_avg)

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

    # --- БАЗА ДЛЯ VO2 / FIT AGE ---
    vo2_calc = vo2_garmin if vo2_garmin > 0 else (vo2_val if vo2_val else 32.7)
    vo2_source = "Garmin" if vo2_garmin > 0 else "Strava"

    # 4. РАСЧЕТ FIT AGE
    base_age = (
        get_bio_age()
        - (vo2_calc - 35) * 0.75
        + (fat - 18) * 0.30
        + (rhr - 51) * 0.25
    )

    freshness_adj = 0.0

    # HRV относительно твоего 14-дневного тренда
    if hrv_14d_avg > 0:
        hrv_delta = hrv - hrv_14d_avg

        if hrv_delta >= 20:
            freshness_adj -= 0.8
        elif hrv_delta >= 10:
            freshness_adj -= 0.5
        elif hrv_delta <= -20:
            freshness_adj += 0.8
        elif hrv_delta <= -10:
            freshness_adj += 0.5

    # Сон
    if sleep_score >= 75:
        freshness_adj -= 0.1
    elif 0 < sleep_score < 60:
        freshness_adj += 0.5

    # Recovery
    if recovery_h <= 6:
        freshness_adj -= 0.3
    elif recovery_h > 24:
        freshness_adj += 0.5
    elif recovery_h > 12:
        freshness_adj += 0.2

    # TSB
    if tsb > 5:
        freshness_adj -= 0.2
    elif tsb < -10:
        freshness_adj += 0.4

    f_age_raw = base_age + freshness_adj
    f_age = round(max(49.0, min(get_bio_age() - 1.0, f_age_raw)), 1)
    
    update_fit_age_in_sheet(today, f_age)
    update_age_in_sheet(today, get_bio_age())

    ##if eftp_val:
        ##update_eftp_in_sheet(today, eftp_val)

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
        
        #---MORNING REPORT
        prompt = (
            f"Ты — АРНИ, стиль: жесткий, лаконичный, уверенный тренер. "
            f"Без хамства, без крика, без панибратства. "
            f"НЕ начинай с фраз типа: 'Слушай сюда', 'Чемпион', 'Боец'. "
            f"Атлет: {round(get_bio_age(), 1)} лет. "

            f"ДАННЫЕ: HRV {int(hrv)}, Пульс {int(rhr)}, Сон {sleep}ч (Глубокий: {deep_sleep}ч), "
            f"Sleep Score: {sleep_score}, Recovery: {recovery_h}ч, TSB {tsb}, Готовность {score}/5. "
            f"Fit Age {f_age}. VO2max: {vo2_calc}. "

            f"Нагрузка 7д: {tss7_sum} (ср {tss7_avg})\n"
            f"HRV тренд: {int(hrv)} vs ср {int(hrv_14d_avg)}\n"
            f"Morning fresh: {morning_is_fresh}\n"
            f"Fatigue flag: {fatigue_flag if fatigue_flag else 'нет'}\n"

            f"\nПРАВИЛА АНАЛИЗА:\n"
            f"{sleep_note}"

            f"- ЛИЧНЫЕ ДИАПАЗОНЫ:\n"
            f"  RHR: 46–54 (норма), <50 отлично, >55 сигнал усталости.\n"
            f"  HRV: <40 низко; 40–70 нижняя зона; 70–95 норма; >95 пик готовности.\n"
            f"- Если Morning fresh = False, отчёт предварительный: не делай жёстких выводов по HRV, пульсу, сну и Recovery.\n"

            f"- Если HRV и пульс в норме (RHR ≤54 и HRV ≥70), не называй состояние истощением.\n"
            f"- HRV <40 — это усталость, но не катастрофа, если пульс в норме.\n"

            f"- Не используй слова 'критический', 'резкий', 'сильнейший стресс', 'срыв восстановления' или 'организм не справляется', если HRV >=30, TSB > -20 и пульс не вышел из нормального диапазона.\n"
            f"- При HRV в диапазоне 30-40 используй более спокойные формулировки: 'заметно ниже нормы', 'просадка вариабельности', 'признаки усталости', 'снижение свежести'.\n"
            f"- Слово 'критический' допустимо только при реально жестком сочетании: HRV <30 или TSB <= -20, особенно если одновременно растет пульс и Recovery остается высоким.\n"
            f"- Если пульс остается хорошим, а база сильной, не драматизируй даже при низком HRV: опиши состояние как утомление или недовосстановление, а не как срыв.\n"
            
            f"- Даже при коротком сне не используй формулировки вроде 'критически низкий сон', 'организм не восстановлен' или 'резкое ограничение', если HRV, пульс и TSB не указывают на явную перегрузку.\n"
            f"- Не обесценивай сильные показатели (Fit Age, пульс, VO2max).\n"
            f"- Если Recovery низкий или нормальный, но сон плохой, не пиши: 'организм свеж', 'полностью восстановился' или 'готов на максимум'. Корректно: восстановление после прошлой нагрузки в целом нормальное, но общая свежесть ограничена сном.\n"

            f"- Если Recovery <= 6ч, не пиши: 'низкий уровень восстановления', 'организм не восстановился', 'нужно полноценно восстановиться' или похожие формулировки.\n"
            f"- Recovery <= 6ч трактуй как свежесть или почти полное восстановление, если нет других тяжёлых сигналов.\n"
            f"- Если HRV не ниже тренда и пульс <= 48, не драматизируй состояние даже при коротком сне.\n"
            f"- Если главный минус только в сне, так и пиши: ограничение дня связано прежде всего со сном, а не с плохим восстановлением после нагрузки.\n"
            f"- При сочетании Recovery <= 6ч, HRV >= среднего тренда и пульса <= 48 базовый вывод: осторожная Z1-Z2, а не восстановление как после тяжёлой перегрузки.\n"
            f"- Не используй одновременно взаимоисключающие формулировки вроде 'утренняя свежесть' и 'низкий уровень восстановления'.\n"

            f"- Fit Age интерпретируй не изолированно, а в связке с VO2max, пульсом, HRV, сном, Recovery и TSB.\n"
            f"- Если Fit Age ухудшился, обязательно коротко объясни причину: это просадка формы или временный эффект усталости/недовосстановления.\n"
            f"- Долгосрочную форму оцени по Fit Age, VO2max и низкому пульсу; краткосрочную свежесть — по HRV, сну, Recovery и TSB.\n"
            f"   Не используй драматичные слова без экстремальных значений; при умеренной просадке описывай состояние сдержанно и точно.\n"
            f"   Обязательно объясни, почему Fit Age такой сегодня: из-за базы или из-за текущего восстановления.\n"
            f"- Если Fit Age выглядит хуже, но VO2max и пульс остаются сильными, не называй это потерей формы: чаще это временная просадка свежести.\n"
            f"- Если Fit Age улучшается, коротко укажи, что именно это поддерживает: сильная база, хороший HRV, низкий пульс или качественное восстановление.\n"
            f"- В пункте 2 не просто упоминай Fit Age, а объясняй, почему он такой сегодня и что в нём временно, а что отражает реальную базу.\n"

            f"- Если Recovery <= 6ч, TSB около 0 и HRV не ниже тренда, не пиши, что организм не восстановился после нагрузки.\n"
            f"- В таком случае разделяй два вывода: по тренировочной нагрузке состояние свежее, но короткий сон снижает общий ресурс, внимательность и субъективную готовность.\n"
            f"- Если главный ограничитель дня — сон, а не накопленная нагрузка, базовый вердикт: Z1 или лёгкая Z2, без Z3 и без интенсивных интервалов.\n"
            
            f"- Если HRV >95 — допускается повышение нагрузки.\n"
            f"- TSB около 0 — это баланс, не отдых и не перегруз.\n"
            f"- TSB от -5 до 0 при высоком HRV не является признаком перегруза.\n"
            f"- Если HRV >95 и пульс <=45, это приоритетный сигнал готовности.\n"
            f"- При HRV >95, пульсе <=45, сне >=7ч и recovery <=6ч не рекомендуй отдых.\n"
            f"- В таком случае базовый вердикт: работа Z2-Z3, а не Z1 и не полный отдых.\n"
            f"- Отдых предлагай только если Recovery >24ч, Sleep Score <60 или HRV реально просел.\n"
            f"- Если утренние данные отсутствуют и используются default значения, явно напиши, что отчёт предварительный.\n"

            f"- Контроль зон: <2.0 отдых; 2.0–3.0 Z1–Z2; 3.0–3.5 осторожно Z2; >3.5 можно Z3.\n"
            f"- Не повторяй точные цифры почти дословно. В большинстве случаев давай интерпретацию, а не пересказ.\n"
            f"- Вместо 'HRV 74, пульс 48, VO2max 43' пиши по смыслу: 'HRV в норме, пульс низкий, аэробная база сильная'.\n"
            f"- Не начинай предложения с голых чисел и метрик вроде 'HRV 74' или 'Пульс 48', если без этого можно обойтись.\n"
            f"- Если показатели хорошие, объясни, что это значит для тренировки сегодня, а не просто перечисляй HRV, RHR и VO2max.\n"
            f"- В каждом пункте сначала дай вывод, потом коротко объясни, почему он такой.\n"
            f"- Не пиши телеграфным стилем и рублеными фразами. Пиши плотно, но естественно.\n"
            f"- Каждый пункт — 2-3 полных предложения, а не набор коротких обрывков.\n"
            f"- Строго соблюдай формат из 3 пунктов.\n"
            f"- Сразу начинай с пункта 1, без вступления.\n"
            f"- Финальная фраза — короткая, на русском, без английского.\n"

            f"- В пункте 1 сначала отделяй сон от recovery: плохой сон не равен плохому восстановлению после нагрузки.\n"

            f"\nВЫДАЙ СТРОГО ПО ПУНКТАМ:\n"
            f"1. СОСТОЯНИЕ: Сначала оцени HRV и пульс, затем только потом TSB.\n"
            f"   Не используй драматичные слова без экстремальных значений; при умеренной просадке описывай состояние сдержанно и точно.\n"
            f"2. АНАЛИЗ: Оценка базы (Fit Age, RHR, VO2max).\n"
            f"   Обязательно объясни, почему Fit Age такой сегодня: из-за базы или из-за текущего восстановления.\n"
            f"3. ВЕРДИКТ: Конкретный план на день с объёмом/интенсивностью, а не общая фраза. Не занижай нагрузку без явных признаков усталости.\n"
        )
       
        fallback_text = (
            f"1. СОСТОЯНИЕ: HRV {int(hrv)} и пульс {int(rhr)} выглядят сильно. "
            f"TSB {tsb} показывает текущий баланс нагрузки.\n"
            f"2. АНАЛИЗ: Fit Age {f_age} и VO2max {vo2_calc} отражают базовую форму, "
            f"а HRV, сон и восстановление помогают понять, насколько это значение связано с текущей свежестью.\n"
            f"3. ВЕРДИКТ: Работай по готовности {score}/5. "
            f"Объём и интенсивность держи под контроль."
        )

        ai_msg = ask_arnie(prompt, fallback_text)
        
        # 1. Сначала определяем текстовый статус (s_status)
        if sleep_score < 55:
            s_status = "Плохо"
        elif sleep_score < 75:
            s_status = "Средне"
        else:
            s_status = "Отлично"

        # 2. И только потом используем его в отчете

        flags = detect_anomalies(hrv, rhr, 0, eftp_val, FTP_GARMIN)
        flags_text = f"⚠️ {' | '.join(flags)}\n\n" if flags else ""
 
        report = (f"🌅 УТРЕННИЙ СТАТУС {status_icon}\n\n"
                  f"{'✅ Утренние данные за сегодня получены' if morning_is_fresh else '⚠️ Утренние данные неполные'}\n"
                  f"{ftp_line}\n"
                  f"{flags_text}"
                  f"❤️ Пульс: {int(rhr)} | 🌀 HRV: {int(hrv)}\n"
                  f"📉 HRV тренд: {int(hrv)} vs ср {int(hrv_14d_avg)}\n"
                  f"🛡 Статус: {day_status}\n"
                  f"🔋 Готовность: {score}/5\n"
                  f"🕒 Восстановление: {rec_text}\n"
                  f"😴 Качество сна: {sleep_score} ({s_status})\n"
                  f"🫁 VO2max: {vo2_calc} ({vo2_source})\n"
                  f"📊 Форма (TSB): Garmin {tsb_garmin if tsb_garmin != 999 else 'н/д'} | Strava {tsb_strava}\n"
                  f"📦 Нагрузка 7д: {tss7_sum} TSS | ср {tss7_avg}/день\n"
                  f"{fatigue_flag + chr(10) if fatigue_flag else ''}"
                  f"🧬 Fit Age: {f_age}\n\n"
                  f"🤖 АРНИ:\n{ai_msg}")

    else:
        # --- АНАЛИЗ ТРЕНИРОВКИ (v2.2) ---
        last = sorted(today_acts, key=lambda x: x.get("start_date_local"))[-1]
        a_type_last = last.get("type", "")
        dist = round(last.get("distance", 0) / 1000, 2)
        name = last.get("name", "Тренировка")
        t_sec = last.get("moving_time", 0)
        dur_min = round(t_sec / 60, 1)
        is_ride = a_type_last in ["Ride", "VirtualRide"]
        is_strength = a_type_last in ["Weight Training", "Workout", "WeightTraining", "Gym"]
        power_trusted = power_is_trusted(last)
        
        # Собираем данные интенсивности
        w_avg = safe_float(last.get("average_watts"), 0)
        if_val = round(w_avg / FTP_GARMIN, 2) if FTP_GARMIN > 0 and w_avg > 0 else 0
        hr_avg = safe_float(last.get("average_heartrate"), 0)
        hr_max_act = safe_float(last.get("max_heartrate"), 0)

        if is_ride:
            distance_prompt = f"Дистанция: {dist} км\n"
        else:
            distance_prompt = ""
            
        # POWER RULES PROMPT
        if is_ride and power_trusted and w_avg > 0:
            power_prompt = (
                f"Средняя мощность: {w_avg} Вт\n"
                f"IF: {if_val}\n"
            )
            power_rules_prompt = (
                f"- Для indoor ride длительностью 20-30 минут с IF до 0.80 и TSS до 25 используй формулировки: лёгкая рабочая аэробная сессия, умеренная рабочая сессия, поддерживающая темповая работа.\n"
                f"- Для такой тренировки не используй слова: плотная, тяжёлая, близкая к пороговой, заметно интенсивная, серьёзный стресс, выраженный стресс.\n"
                f"- Для indoor ride 20-30 минут с IF 0.80-0.85 и TSS до 35 пиши: короткая рабочая аэробная или темповая сессия, без признаков предельной нагрузки.\n"
                f"- Только если IF > 0.85 или TSS > 35 для короткой сессии 20-30 минут, можно писать: плотная работа.\n"
                f"- IF < 0.75 = лёгкая или умеренная работа.\n"
                f"- IF 0.75-0.85 = уверенная рабочая интенсивность, но не экстремальная.\n"
                f"- IF 0.86-0.90 = тяжёлая работа.\n"
                f"- Если IF >= 0.90, прямо говори: работа высокая, близкая к пороговой.\n"
            )
        elif is_ride:
            power_prompt = ""
            power_rules_prompt = ""
        else:
            power_prompt = ""
            power_rules_prompt = ""

        # --- LONG OUTDOOR RIDE RULES ---
        name_lc = str(name).lower()
        is_indoor_like = (
            a_type_last == "VirtualRide"
            or "зальный велоспорт" in name_lc
            or "indoor" in name_lc
            or "virtual" in name_lc
            or "technogym" in name_lc
            or bool(last.get("trainer"))
        )

        if is_ride and not is_indoor_like and dur_min >= 90:
            elev_gain = safe_float(last.get("total_elevation_gain"), 0)

            long_ride_rules = (
                f"- Если длительность >= 90 минут, классифицируй тренировку как длительную аэробную (endurance).\n"
                f"- Если длительность >= 150 минут, используй формулировки: длительная, объёмная, базовая тренировка на выносливость.\n"
                f"- Для длительных поездок НЕ используй формулировки: лёгкая тренировка, короткая сессия.\n"
                f"- Даже при низком IF/TSS подчёркивай объём: значительный объём работы.\n"
                f"- Для длительной поездки около 3 часов с TSS в диапазоне 55-80 не называй нагрузку низкой. Используй: умеренная, контролируемая или заметная, но не тяжёлая.\n"
                f"- Если Effort <= 3, используй: контролируемая, комфортная, хорошо перенесённая нагрузка.\n"
                f"- Если Feel >= 4, добавляй: тренировка прошла уверенно и без признаков перегрузки.\n"
                f"- Для длительных поездок НЕ делай акцент на IF как на главном показателе.\n"
                f"- Главный акцент: длительность, объём, стабильность работы.\n"
                f"- Если большая часть времени в Z1-Z2, пиши: работа в аэробной зоне с контролируемым темпом.\n"
                f"- Если есть выходы в Z3 на рельефе, формулируй: естественные повышения интенсивности на подъёмах.\n"
                f"- После длительных поездок (>=150 мин) в разделе 'ЗАВТРА' НЕ предлагай рабочие тренировки.\n"
                f"- Используй: восстановительная активность, Z1 или лёгкая Z2, без акцента на нагрузку.\n"
                f"- Если outdoor ride длится около 150-210 минут, имеет дистанцию примерно 45-60 км или набор высоты 500+ м, не называй её просто рабочим объёмом или лёгкой сессией.\n"
                f"- Для такой тренировки используй формулировки: длинная объёмная аэробная поездка, серьёзная базовая работа на выносливость, заметная, но контролируемая нагрузка.\n"
                f"- Для длительной поездки около 3 часов с TSS в диапазоне 55-80 и набором высоты 500+ м не занижай значимость сессии. Это полноценная большая база, а не просто поддержание формы.\n"
                f"- В пункте 'СТАТУС' для такой сессии используй уровень формулировок: большой аэробный объём, существенная базовая работа, длинная поездка на выносливость.\n"
                f"- Если Feel >= 4 и Effort <= 2-3, подчёркивай: объём был большим, но тренировка перенесена легко, уверенно и экономично.\n"
            )

            if elev_gain > 300:
                long_ride_rules += (
                    f"- Обязательно упомяни рельеф: холмистый маршрут, набор высоты, работа на подъёмах.\n"
                )

            if elev_gain > 600:
                long_ride_rules += (
                    f"- Подчеркни: значительный рельеф или существенный набор высоты.\n"
                )

            power_rules_prompt += long_ride_rules
     
        # РАСЧЁТ TSS
        tss_last = calc_activity_tss(last, FTP_GARMIN)

        if is_strength:
            subjective_prompt = (
                f"Strength_Feel: {strength_feel}\n"
                f"Strength_Effort: {strength_effort}\n\n"
            )
        elif is_ride:
            subjective_prompt = (
                f"Ride_Feel: {ride_feel}\n"
                f"Ride_Effort: {ride_effort}\n\n"
            )
        else:
            subjective_prompt = ""

        # Формируем умный промпт для анализа нагрузки
        #---ACTIVITY REPORT
        prompt = (
            f"Ты — АРНИ, точный, уверенный, опытный тренер. "
            f"Пиши спокойно, плотно и по делу. "
            f"Без хамства, без дешёвой мотивации, без пафоса, без пустых фраз и без театральных вставок. "
            f"Нужен живой разбор тренировки по цифрам, а не шаблон и не пересказ показателей.\n"

            f"Атлет: {round(get_bio_age(), 1)} лет.\n"
            f"Тренировка: {name}\n"
            f"Тип: {a_type_last}\n"
            f"Длительность: {dur_min} мин\n"
            f"{distance_prompt}"
            f"TSS: {tss_last}\n"
            f"{power_prompt}"
            f"Пульс ср/макс: {hr_avg}/{hr_max_act}\n"
            f"Утренняя готовность: {score}/5\n"
            f"{subjective_prompt}"

            f"ПРАВИЛА РАЗБОРА:\n"
            f"- Это должен быть именно анализ, а не сухое повторение цифр.\n"
            f"- Сначала дай смысл нагрузки, потом опирайся на цифры.\n"
            f"- Обязательно интерпретируй цифры: что они значат по интенсивности, плотности и переносимости работы.\n"
            f"- Разбирай только текущую тренировку, а не весь день целиком.\n"

            f"- Если текущая тренировка силовая, опирайся в первую очередь на Strength_Feel / Strength_Effort.\n"
            f"- Если текущая тренировка вело, опирайся в первую очередь на Ride_Feel / Ride_Effort.\n"
            f"- Не обсуждай силовую в разборе велотренировки, если силовая не анализируется прямо сейчас.\n"
            f"- Не обсуждай вело в разборе силовой, если вело не анализируется прямо сейчас.\n"
            f"- Для indoor ride 20-30 минут с IF 0.80-0.85 и TSS до 35 НЕ используй слова: плотная, тяжёлая, заметно интенсивная. Корректно: короткая рабочая аэробная или темповая сессия, уверенная, но не предельная.\n"

            f"- Feel: 1=Very Weak, 2=Weak, 3=Normal, 4=Strong, 5=Very Strong.\n"
            f"- Effort по шкале Garmin: 1=очень легко, 2=легко, 3=умеренно, 4=довольно тяжело, 5-6=трудно, 7-8=очень трудно, 9=очень тяжело, 10=максимум.\n"
            f"- Не называй Effort 4-5 экстремальной нагрузкой. Это рабочая, но не предельная тяжесть.\n"
            f"- Effort 7-8 = реально тяжёлая работа. Effort 9-10 = предельная работа.\n"

            f"- ШАБЛОНЫ СИЛОВЫХ ДНЕЙ:\n"
            f"  Chest and abs = 4 упражнения на грудь, каждое 4x10, плюс пресс 2x30.\n"
            f"  Back and abs = 4 упражнения на спину, каждое 4x10, плюс пресс 2x30.\n"
            f"  Legs and abs = 4 упражнения на ноги, каждое 4x10, плюс пресс 2x30.\n"
            f"  Arms and abs = 3 упражнения на бицепс и 3 упражнения на трицепс, все по 4x10, плюс пресс 2x30.\n"
            f"  Shoulders, calves and abs = 4 упражнения на плечи по 4x10, 3 упражнения на икры по 4x15, плюс пресс 2x30.\n"
            f"- Эти шаблоны нужны только для понимания стандартного планового объёма.\n"
            f"- Не делай выводы о технике, подборе весов, качестве выполнения, прогрессии или идеально выполненном плане, если в данных нет весов, повторений по факту, RPE или другой детальной структуры.\n"
            f"- Если Strength_Effort высокий или Strength_Feel низкий, значит стандартный объём дался тяжелее обычного.\n"
            f"- Если Strength_Effort умеренный и Strength_Feel высокий, значит объём перенесён хорошо.\n"
            f"- Для силовой без детальных данных допустимы выводы уровня: нагрузка выглядела контролируемой, сессия перенесена нормально, без явных признаков перегруза.\n"

            f"{power_rules_prompt}"

            f"- Если тренировка короче 30 минут, но интенсивность действительно высокая по мощности или по среднему пульсу, можно писать: короткая, но плотная и качественная работа.\n"
            f"- Если у вело нет trusted power, не используй язык power-аналитики: IF, NP, watts, power zones, пороговая мощность, работа по мощности.\n"
            f"- Если у вело нет мощности, оцени тяжесть в первую очередь по среднему пульсу, длительности, TSS и субъективной оценке, а не по скорости и не по IF.\n"
            f"- Не делай вывод 'высокая скорость = высокая интенсивность': на это влияют рельеф, ветер, трафик и спуски.\n"
            f"- Если Ride Effort = 1-2 и TSS ниже 20, не называй работу интенсивной, плотной, тяжёлой, анаэробной или пороговой. Это лёгкая или лёгко-умеренная поездка.\n"
            f"- Если Ride Feel = 4 и Ride Effort = 2, трактуй это как приятную, хорошо перенесённую лёгкую работу, а не как противоречие.\n"
            f"- Для короткой лёгкой велосессии с низким TSS и длительностью около 20-30 минут пиши короче: поддерживающая или восстановительная аэробная работа без выраженного стресса.\n"
            f"- Не раздувай значение короткой лёгкой сессии.\n"

            f"- Если это outdoor Ride, а не VirtualRide, и поездка длится около 20-30 минут при дистанции примерно 7-10 км и среднем пульсе около 120+ уд/мин, не называй её нулевой, пустой или минимальной активностью. Это короткая, но полноценная поддерживающая поездка.\n"
            f"- Если TSS низкий, но Ride Feel = 3-4 и Ride Effort = 3, трактуй такую сессию как лёгкую рабочую аэробную нагрузку, а не как почти отсутствие нагрузки.\n"
            f"- Для такой поездки используй формулировки уровня: короткая поддерживающая поездка, лёгкая рабочая аэробная сессия, спокойная, но полезная нагрузка.\n"
            
            f"- Для outdoor Ride длительностью около 20-30 минут с TSS ниже 12 и Ride Effort 3 не завышай значимость сессии.\n"
            f"- Называй такую работу короткой поддерживающей или лёгкой рабочей аэробной поездкой.\n"
            f"- Не используй формулировки вроде 'продуктивная аэробная работа', 'качественная сессия' или 'заметная нагрузка', если TSS очень низкий.\n"
            f"- Не опирайся на разовый max HR в такой короткой тренировке и не делай по нему вывод о тяжести.\n"
            f"- Если TSS ниже 12 и субъективно поездка перенесена хорошо, итог должен быть: лёгкая полезная активность без значимого стресса.\n"
            
            f"- Не делай вывод по разовому max HR. Для общей оценки важнее средний пульс и субъективное усилие.\n"
            f"- Не советуй полный отдых автоматически только из-за одной короткой тренировки. Для завтра выбирай режим по реальной тяжести работы.\n"
            f"- После силовой с TSS примерно до 25-30, длительностью около 45-60 минут и Strength Effort 4 не назначай полный отдых как основной сценарий. Базовый вариант на завтра — Z1 или лёгкая Z2, если нет явных признаков перегруза.\n"
            f"- Полный отдых после силовой предлагай только если одновременно есть признаки высокой усталости: низкий HRV, плохой сон, высокий Recovery Time, тяжёлое субъективное состояние или явно высокая суммарная нагрузка.\n"
            f"- Если велосессия очень короткая и лёгкая: длительность около 10-20 минут и TSS ниже 10, не пиши про возможную интенсивную работу на завтра. Базовый вывод — Z1 или Z2, без намёков на тяжёлую сессию.\n"
            f"- После очень лёгкой поездки не используй формулировки вроде 'можно более интенсивную работу согласно циклу', если в данных нет явных признаков высокой готовности и отдельного запроса на это.\n"
            f"- Для завтра выбирай: отдых, Z1 или Z2. Но обязательно коротко объясни выбор по нагрузке.\n"

            f"- Не повторяй одну и ту же мысль в пунктах 1 и 2.\n"
            f"- Пункт 1 — характер нагрузки и общий вердикт.\n"
            f"- Пункт 2 — что по этой тренировке реально получилось и как она была перенесена.\n"
            f"- Пункт 3 — что делать завтра и почему.\n"
            f"- Каждый пункт 2-4 предложения, не одно.\n"

            f"- Не используй театральные или пустые финальные фразы вроде: 'Слушай внимательно', 'Работай', 'Двигайся дальше', 'Держи темп'.\n"
            f"- Финальная строка, если она есть, должна быть коротким деловым итогом по состоянию, а не лозунгом.\n"
            f"- Пиши по-русски, плотно, спокойно, точно, без украшательства и без категоричных выводов из неполных данных.\n\n"

            f"- После короткой, но интенсивной велосессии длительностью около 20-30 минут с IF около 0.85-0.90 и TSS около 25-35 не назначай строго только Z1 как единственный вариант. Базовый вывод на завтра — Z1 или лёгкая Z2, если нет явных признаков накопленной усталости.\n"
            f"- Формулировки для завтра не делай слишком узкими без явных причин. Если восстановление выглядит нормальным, пиши: Z1 или лёгкая Z2, а не только Z1.\n"
            f"- В пункте ЗАВТРА опирайся только на текущую анализируемую тренировку и текущее состояние. Не пиши про вчерашнюю работу, если она явно не дана во входных данных.\n"
            f"- Если IF высокий, но TSS невысокий из-за короткой длительности, пиши: короткая, но плотная рабочая сессия; суммарный стресс невысокий, но интенсивность заметная.\n"
            
            f"ОТВЕТ СТРОГО ПО ПУНКТАМ:\n"
            f"1. СТАТУС:\n"
            f"2. ФИДБЕК:\n"
            f"3. ЗАВТРА:\n\n"

            f"В конце дай одну короткую итоговую фразу на русском без пафоса и без мотивационных лозунгов."
        )

        if is_ride and power_trusted and w_avg > 0 and dur_min >= 90:
            fallback_text = (
                f"1. СТАТУС: Это была длинная объёмная аэробная поездка, а не просто обычная сессия. "
                f"За {dur_min} минут ты выполнил серьёзную базовую работу на выносливость с заметным, но контролируемым стрессом.\n"
                f"2. ФИДБЕК: Дистанция {dist} км, TSS {tss_last} и пульс {hr_avg}/{hr_max_act} показывают существенный объём при управляемой интенсивности. "
                f"Если сессия субъективно прошла легко, значит нагрузка была перенесена уверенно и экономично, без явных признаков перегруза.\n"
                f"3. ЗАВТРА: Базовый вариант — восстановительная Z1 или очень лёгкая Z2, только если ноги ощущаются нормально. "
                f"Если есть тяжесть или забитость, тогда только восстановление."
            )
        elif is_ride and power_trusted and w_avg > 0:
            fallback_text = (
                f"1. СТАТУС: Это была рабочая тренировка. "
                f"IF {if_val} и средняя мощность {w_avg} Вт для {dur_min} минут показывают, что нагрузка была ощутимой, но не обязательно предельной.\n"
                f"2. ФИДБЕК: Ты ровно провёл сессию и удержал рабочий темп по ходу тренировки. "
                f"Пульс {hr_avg}/{hr_max_act} и TSS {tss_last} подтверждают качественную нагрузку без явных признаков развала.\n"
                f"3. ЗАВТРА: Выбор между отдыхом, Z1 или лёгкой Z2 делай по утреннему состоянию. "
                f"Жёсткий отдых нужен не автоматически, а только если утром просядут HRV, сон или появится заметная тяжесть."
            )
        else:
            fallback_text = (
                f"1. СТАТУС: Это была рабочая тренировка. "
                f"Длительность {dur_min} минут и TSS {tss_last} показывают, что нагрузка была ощутимой, но не обязательно предельной.\n"
                f"2. ФИДБЕК: Ты ровно провёл сессию и удержал рабочий темп по ходу тренировки. "
                f"Пульс {hr_avg}/{hr_max_act} и TSS {tss_last} подтверждают качественную нагрузку без явных признаков развала.\n"
                f"3. ЗАВТРА: Выбор между отдыхом, Z1 или лёгкой Z2 делай по утреннему состоянию. "
                f"Жёсткий отдых нужен не автоматически, а только если утром просядут HRV, сон или появится заметная тяжесть."
            )
            
        ai_msg = ask_arnie(prompt, fallback_text)

        subjective_line = ""
        if is_strength:
            subjective_line = f"🏋️ Силовая: Feel {strength_feel if strength_feel > 0 else 'н/д'} | Effort {strength_effort if strength_effort > 0 else 'н/д'}\n"
        elif is_ride:
            subjective_line = f"🚴 Вело: Feel {ride_feel if ride_feel > 0 else 'н/д'} | Effort {ride_effort if ride_effort > 0 else 'н/д'}\n"

        ftp_context_line = ""
        if is_ride and eftp_is_eligible(last) and eftp_val is not None and eftp_val > 0:
           delta = int(round(eftp_val - FTP_GARMIN))
           ftp_context_line = f"🚴 FTP/eFTP: {FTP_GARMIN}/{int(round(eftp_val))} ({delta:+}){eftp_trend_text}\n"
        elif is_ride:
           ftp_context_line = f"🚴 FTP: {FTP_GARMIN}\n"

        if is_ride:
            header_line = f"📍 {dist} км | ⏱ {dur_min} мин"
        else:
            header_line = f"⏱ {dur_min} мин"

        report = (f"🏃 ТРЕНИРОВКА {status_icon}\n\n"
                  f"<b>{html.escape(name)}</b>\n"
                  f"{ftp_context_line}"
                  f"{header_line}\n"
                  f"📈 TSS: {tss_last}\n"
                  f"{subjective_line}\n"
                  f"🤖 АРНИ:\n{ai_msg}")
    send_tg(report)

if __name__ == "__main__":
    main()
