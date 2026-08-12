import os
os.environ["HTTPS_PROXY"] = ""

import datetime
import sqlite3
import requests
import pytz
import threading
from transliterate import translit
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

# ===== ВЕБ-СЕРВЕР ДЛЯ RENDER =====
from flask import Flask

app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host='0.0.0.0', port=port)

threading.Thread(target=run_flask, daemon=True).start()
# ===== КОНЕЦ =====

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8981103282:AAGjgC67pgW6xKMh2d2hg57ZygXuUr1bnr0")

# --- БАЗА ДАННЫХ ---

def init_db():
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            last_name TEXT,
            username TEXT,
            default_city TEXT,
            first_seen TEXT
        )
    """)
    
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN timezone TEXT")
    except sqlite3.OperationalError:
        pass
    
    conn.commit()
    conn.close()

def save_user(update: Update):
    user = update.effective_user
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT OR IGNORE INTO users (user_id, first_name, last_name, username, first_seen)
        VALUES (?, ?, ?, ?, ?)
    """, (
        user.id,
        user.first_name,
        user.last_name,
        user.username,
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))
    
    conn.commit()
    conn.close()

def set_user_city(user_id: int, city: str, timezone: str):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET default_city = ?, timezone = ? WHERE user_id = ?", 
                   (city, timezone, user_id))
    conn.commit()
    conn.close()

def get_user_data(user_id: int):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT default_city, timezone FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result if result else (None, None)

def get_user_city(user_id: int):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT default_city FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

def get_all_users():
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, default_city FROM users WHERE default_city IS NOT NULL")
    result = cursor.fetchall()
    conn.close()
    return result

# --- ПОГОДА ---

def get_weather_for_city(city_ru: str):
    try:
        city_lat = translit(city_ru, 'ru', reversed=True)
    except:
        city_lat = city_ru
    
    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city_lat}&count=1&language=ru"
    geo_response = requests.get(geo_url)
    geo_data = geo_response.json()
    
    if not geo_data.get("results"):
        return None
    
    lat = geo_data["results"][0]["latitude"]
    lon = geo_data["results"][0]["longitude"]
    name = geo_data["results"][0]["name"]
    country = geo_data["results"][0].get("country", "")
    
    weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=temperature_2m,weathercode&timezone=auto&forecast_days=1"
    weather_response = requests.get(weather_url)
    weather_data = weather_response.json()
    
    hourly = weather_data["hourly"]
    
    morning_temp = hourly["temperature_2m"][6]
    day_temp = hourly["temperature_2m"][12]
    evening_temp = hourly["temperature_2m"][18]
    
    morning_code = hourly["weathercode"][6]
    day_code = hourly["weathercode"][12]
    evening_code = hourly["weathercode"][18]
    
    def is_rain(code):
        return code in [51, 53, 55, 61, 63, 65, 80, 81, 82]
    
    rain_morning = "☔️" if is_rain(morning_code) else "☀️"
    rain_day = "☔️" if is_rain(day_code) else "☀️"
    rain_evening = "☔️" if is_rain(evening_code) else "☀️"
    
    text = (
        f"🌤 Прогноз погоды на сегодня в {name}, {country}:\n\n"
        f"🌅 Утро: {morning_temp}°C {rain_morning}\n"
        f"☀️ День: {day_temp}°C {rain_day}\n"
        f"🌆 Вечер: {evening_temp}°C {rain_evening}\n\n"
        f"☔️ — дождь | ☀️ — без осадков"
    )
    
    return text

def get_timezone_for_city(city_ru: str):
    try:
        city_lat = translit(city_ru, 'ru', reversed=True)
    except:
        city_lat = city_ru
    
    geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={city_lat}&count=1"
    geo_response = requests.get(geo_url)
    geo_data = geo_response.json()
    
    if not geo_data.get("results"):
        return None
    
    lat = geo_data["results"][0]["latitude"]
    lon = geo_data["results"][0]["longitude"]
    
    tz_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&timezone=auto&forecast_days=1"
    tz_response = requests.get(tz_url)
    tz_data = tz_response.json()
    
    return tz_data.get("timezone", "Europe/Moscow")

# --- ЕЖЕДНЕВНАЯ РАССЫЛКА ---

async def daily_forecast(context: ContextTypes.DEFAULT_TYPE):
    users = get_all_users()
    sent_count = 0
    error_count = 0
    
    for user_id, city in users:
        try:
            forecast = get_weather_for_city(city)
            if forecast:
                await context.bot.send_message(chat_id=user_id, text=forecast)
                sent_count += 1
                print(f"📤 Отправлено пользователю {user_id}")
            else:
                error_count += 1
        except Exception as e:
            error_count += 1
            print(f"❌ Ошибка для {user_id}: {e}")
    
    print(f"📊 Итог: отправлено {sent_count}, ошибок {error_count}")

# --- КОМАНДЫ ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    save_user(update)
    user = update.effective_user
    
    keyboard = [
        [KeyboardButton("🕐 Время")],
        [KeyboardButton("ℹ️ Помощь")],
        [KeyboardButton("👤 Инфо")],
        [KeyboardButton("🌤 Погода")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"👋 Привет, {user.first_name}! Я бот-помощник.\n\n"
        "Нажимай кнопки ниже или пиши команды:\n"
        "/start — приветствие\n"
        "/help — помощь\n"
        "/time — текущее время\n"
        "/info — информация о пользователе\n"
        "/weather [город] — погода в городе\n"
        "/setcity [город] — сохранить город по умолчанию\n\n"
        "📌 Я буду присылать прогноз погоды каждое утро в 6:00!",
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 Доступные команды:\n"
        "/start — показать приветствие\n"
        "/help — эта справка\n"
        "/time — текущее время\n"
        "/info — информация о пользователе\n"
        "/weather [город] — погода в городе\n"
        "/setcity [город] — сохранить город по умолчанию\n\n"
        "📌 Я присылаю прогноз на день каждое утро в 6:00!"
    )

async def time_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    city, timezone_str = get_user_data(user_id)
    
    if not timezone_str:
        await update.message.reply_text(
            "⚠️ Сначала установите город с помощью команды:\n/setcity Москва"
        )
        return
    
    tz = pytz.timezone(timezone_str)
    now = datetime.datetime.now(tz)
    time_str = now.strftime("%H:%M:%S")
    
    await update.message.reply_text(f"🕐 Текущее время в {city}: {time_str}")

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    first_name = user.first_name or "Не указано"
    last_name = user.last_name or "Не указано"
    username = user.username or "Не указан"
    
    city, timezone = get_user_data(user_id)
    city = city or "Не указан"
    timezone = timezone or "Не указан"
    
    text = (
        f"👤 Информация о пользователе:\n\n"
        f"🆔 ID: {user_id}\n"
        f"📛 Имя: {first_name}\n"
        f"📛 Фамилия: {last_name}\n"
        f"🔹 Username: @{username}\n"
        f"🏙 Город: {city}\n"
        f"🕐 Часовой пояс: {timezone}"
    )
    
    await update.message.reply_text(text)

async def setcity_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "🏙 Укажите город после команды.\n"
            "Пример: /setcity Москва"
        )
        return
    
    city = " ".join(context.args)
    user_id = update.effective_user.id
    
    timezone = get_timezone_for_city(city)
    
    if not timezone:
        await update.message.reply_text(f"❌ Город '{city}' не найден. Попробуйте другой.")
        return
    
    set_user_city(user_id, city, timezone)
    
    await update.message.reply_text(
        f"✅ Город '{city}' сохранён как город по умолчанию.\n"
        f"🕐 Часовой пояс: {timezone}\n"
        "Теперь я буду показывать время и погоду для этого города!"
    )

async def weather_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    if context.args:
        city_ru = " ".join(context.args)
    else:
        city_ru = get_user_city(user_id)
        if not city_ru:
            await update.message.reply_text(
                "🌤 Укажите город после команды.\n"
                "Пример: /weather Москва\n"
                "Или установите город по умолчанию: /setcity Москва"
            )
            return
    
    await update.message.reply_text(f"🔍 Ищу прогноз для {city_ru}...")
    
    forecast = get_weather_for_city(city_ru)
    
    if forecast:
        await update.message.reply_text(forecast)
    else:
        await update.message.reply_text(f"❌ Город '{city_ru}' не найден. Попробуйте на английском.")

# --- ОБРАБОТЧИК ТЕКСТОВ ---

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    
    if text == "🕐 Время":
        await time_command(update, context)
    elif text == "ℹ️ Помощь":
        await help_command(update, context)
    elif text == "👤 Инфо":
        await info_command(update, context)
    elif text == "🌤 Погода":
        await weather_command(update, context)
    else:
        await update.message.reply_text(
            "❌ Я не понимаю эту команду.\n"
            "Используй /help для списка команд или нажми кнопку."
        )

# --- ГЛАВНАЯ ФУНКЦИЯ ---

def main():
    init_db()
    
    app = Application.builder().token(TOKEN).connect_timeout(30).read_timeout(30).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("time", time_command))
    app.add_handler(CommandHandler("info", info_command))
    app.add_handler(CommandHandler("setcity", setcity_command))
    app.add_handler(CommandHandler("weather", weather_command))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    job_queue = app.job_queue
    
    if job_queue:
        job_queue.run_daily(
            daily_forecast,
            time=datetime.time(hour=6, minute=0),
            days=tuple(range(7))
        )
        print("⏰ Ежедневная рассылка настроена на 6:00")
    else:
        print("⚠️ JobQueue не доступна — рассылка не будет работать")
    
    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
