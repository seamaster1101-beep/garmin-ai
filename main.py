import os
import json
import datetime
from dotenv import load_dotenv
from garminconnect import Garmin
from telegram import Bot
from openai import OpenAI

load_dotenv()

GARMIN_EMAIL = os.getenv("GARMIN_EMAIL")
GARMIN_PASSWORD = os.getenv("GARMIN_PASSWORD")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_KEY)
bot = Bot(token=TELEGRAM_TOKEN)

HISTORY_FILE = "history.json"


def load_history():
    if not os.path.exists(HISTORY_FILE):
        return {"history": {}, "last_workout_id": None}
    with open(HISTORY_FILE, "r") as f:
        return json.load(f)


def save_history(data):
    with open(HISTORY_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_garmin_data():
    garmin = Garmin(GARMIN_EMAIL, GARMIN_PASSWORD)
    garmin.login()

    today = datetime.date.today().isoformat()

    sleep = garmin.get_sleep_data(today)
    hrv = garmin.get_hrv_data(today)
    resting = garmin.get_rhr_day(today)
    body = garmin.get_body_battery(today)

    activities = garmin.get_activities(0, 1)

    return {
        "date": today,
        "sleep": sleep,
        "hrv": hrv,
        "resting_hr": resting,
        "body_battery": body,
        "last_activity": activities[0] if activities else None
    }


def generate_ai_comment(data):
    prompt = f"""
    Проанализируй данные спортсмена:

    Сон: {data['sleep']}
    HRV: {data['hrv']}
    Resting HR: {data['resting_hr']}
    Body Battery: {data['body_battery']}

    Дай краткий утренний анализ состояния и рекомендацию.
    """

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )

    return response.choices[0].message.content


def send_message(text):
    bot.send_message(chat_id=CHAT_ID, text=text)


def main():
    history = load_history()
    data = get_garmin_data()

    today = data["date"]

    history["history"][today] = data

    # Проверка новой тренировки
    workout = data["last_activity"]
    if workout:
        workout_id = workout["activityId"]
        if workout_id != history.get("last_workout_id"):
            history["last_workout_id"] = workout_id
            ai_text = generate_ai_comment(data)
            send_message("🏃 Новая тренировка!\n\n" + ai_text)

    # Утренний отчёт
    if today not in history["history"] or len(history["history"]) == 1:
        ai_text = generate_ai_comment(data)
        send_message("🌅 Утренний отчёт\n\n" + ai_text)

    save_history(history)


if __name__ == "__main__":
    main()
