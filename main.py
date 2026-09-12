from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl import functions
import asyncio
import re
import aiohttp
from flask import Flask
import threading
import os
import traceback
import json

# --- НАСТРОЙКИ ---
api_id = 37868561
api_hash = 'c572dbecd109072ab7ef2935d265b0d8'
session_string = os.environ.get('SESSION_STRING', '')

# Токен для MRKT API (нужно получить отдельно, см. ниже)
MRKT_TOKEN = os.environ.get('MRKT_TOKEN', '')

client = TelegramClient(StringSession(session_string), api_id, api_hash)

GROUP_ID = -1004329127019
TOPICS = {
    'до 3': 3, '3-10': 5, '10-30': 7, '30-50': 9,
    '50-100': 11, '100-500': 13, '500+': 19,
}
WATCH_ACCOUNTS = ['mrktbank', 'giftstoportals']
PRICE_BOT = 'PriceNFTbot'

# Хранилище
seen_gift_slugs = set()
seen_mrkt_gifts = set()  # Для отслеживания новых лотов на MRKT
pending_price_requests = {}  # {msg_id: gift_slug}

app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return 'OK', 200

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def extract_price(text):
    """Ищем цену в ответе бота (например, 'Floor: 4.35 TON')"""
    m = re.search(r'Floor[:\s]+([\d.,]+)\s*(?:TON|GRAM|⭐|USDT)?', text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(',', '.'))
        except ValueError:
            pass
    return None

def get_thread_id(price):
    """Определяем ID темы по цене."""
    if price is None:
        return None
    if price < 3: return TOPICS['до 3']
    elif price < 10: return TOPICS['3-10']
    elif price < 30: return TOPICS['10-30']
    elif price < 50: return TOPICS['30-50']
    elif price < 100: return TOPICS['50-100']
    elif price < 500: return TOPICS['100-500']
    else: return TOPICS['500+']

async def send_to_group(notification, price):
    """Отправляет уведомление в нужную тему группы."""
    thread_id = get_thread_id(price)
    if thread_id is None:
        print(f'[!] Не удалось определить тему для цены {price}', flush=True)
        return
    try:
        await client.send_message(GROUP_ID, notification, reply_to=thread_id)
        print(f'[+] Отправлено в тему {thread_id}: {notification[:100]}...', flush=True)
    except Exception as e:
        print(f'[!] Ошибка при отправке в группу: {e}', flush=True)

# --- ОБРАБОТЧИКИ СОБЫТИЙ ---

@client.on(events.NewMessage(chats=PRICE_BOT))
async def price_bot_handler(event):
    """Слушаем ответы от @PriceNFTbot."""
    if event.out: return
    if event.message.reply_to_msg_id in pending_price_requests:
        gift_slug = pending_price_requests.pop(event.message.reply_to_msg_id)
        text = event.message.text
        if not text: return
        price = extract_price(text)
        if price is None:
            print(f'[!] Не удалось определить цену для {gift_slug}', flush=True)
            return
        gift_link = f'https://t.me/nft/{gift_slug}'
        notification = (
            f'📊 Новый подарок: {gift_link}\n'
            f'💰 Флор: {price} TON\n'
            f'🕒 Обновлено'
        )
        await send_to_group(notification, price)

@client.on(events.Raw(types=functions.payments.GetSavedStarGiftsRequest))
async def upgrade_handler(event):
    """Слушаем события об апгрейде подарков."""
    # В реальности здесь будет более сложная логика для отслеживания апгрейдов.
    # Пока просто заглушка для примера.
    print(f'[UPGRADE] Обнаружено событие апгрейда: {event}', flush=True)

async def check_profile(username):
    """Проверяем профиль пользователя на новые подарки."""
    try:
        result = await client(functions.payments.GetSavedStarGiftsRequest(
            peer=username, limit=100
        ))
        new_gifts = []
        for gift in result.gifts:
            if hasattr(gift, 'slug') and gift.slug and gift.slug not in seen_gift_slugs:
                seen_gift_slugs.add(gift.slug)
                new_gifts.append(gift.slug)
        if new_gifts:
            print(f'[+] @{username}: найдено {len(new_gifts)} новых подарков', flush=True)
            for slug in new_gifts:
                sent_msg = await client.send_message(PRICE_BOT, f'https://t.me/nft/{slug}')
                pending_price_requests[sent_msg.id] = slug
                print(f'[PRICE_BOT] Запрос для {slug}', flush=True)
        else:
            print(f'[=] @{username}: новых подарков нет', flush=True)
    except Exception as e:
        print(f'[!] Ошибка при проверке @{username}: {e}', flush=True)

async def check_mrkt_listings():
    """Проверяем новые лоты на MRKT через API."""
    if not MRKT_TOKEN:
        return
    url = 'https://api.tgmrkt.io/api/v1/gifts/saling'
    headers = {'token': MRKT_TOKEN}
    # Запрашиваем последние подарки, сортировка по времени (None)
    json_data = {
        "collectionNames": [], "modelNames": [], "backdropNames": [], "symbolNames": [],
        "ordering": None, "lowToHigh": True, "maxPrice": None, "minPrice": None,
        "mintable": None, "number": None, "count": 20, "cursor": '', "query": None,
        "promotedFirst": False,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=json_data, headers=headers) as resp:
                if resp.status != 200:
                    print(f'[MRKT] Ошибка API: {resp.status}', flush=True)
                    return
                data = await resp.json()
                gifts = data.get('gifts', [])
                for gift in gifts:
                    # Используем уникальный идентификатор подарка на маркете
                    gift_id = gift.get('id')  # или другой уникальный ключ
                    if gift_id and gift_id not in seen_mrkt_gifts:
                        seen_mrkt_gifts.add(gift_id)
                        # Формируем уведомление о новом лоте
                        gift_link = f"https://t.me/nft/{gift.get('slug')}"
                        price = gift.get('price')
                        notification = (
                            f'🛒 Новый лот на MRKT: {gift_link}\n'
                            f'💰 Цена: {price} TON\n'
                            f'📦 Коллекция: {gift.get("collectionName")}'
                        )
                        await send_to_group(notification, price)
    except Exception as e:
        print(f'[MRKT] Ошибка: {e}', flush=True)

async def profile_monitor():
    """Бесконечный цикл проверки профилей."""
    while True:
        for username in WATCH_ACCOUNTS:
            await check_profile(username)
        await asyncio.sleep(120)  # Проверяем каждые 2 минуты

async def mrkt_monitor():
    """Бесконечный цикл проверки новых лотов на MRKT."""
    while True:
        await check_mrkt_listings()
        await asyncio.sleep(30)  # Проверяем каждые 30 секунд

async def main():
    try:
        print('[MAIN] Подключаюсь к Telegram...', flush=True)
        await client.start()
        me = await client.get_me()
        print(f'[+] Подключён как: @{me.username}', flush=True)

        try:
            group = await client.get_entity(GROUP_ID)
            print(f'[+] Группа найдена: {group.title}', flush=True)
        except Exception as e:
            print(f'[!] Не могу получить группу {GROUP_ID}: {e}', flush=True)

        # Запускаем мониторинг в фоне
        asyncio.create_task(profile_monitor())
        if MRKT_TOKEN:
            asyncio.create_task(mrkt_monitor())
            print('🚀 Парсер запущен. Слежу за профилями и MRKT...', flush=True)
        else:
            print('🚀 Парсер запущен. Слежу за профилями (MRKT выключен)...', flush=True)

        await client.run_until_disconnected()
    except Exception as e:
        print(f'[!!!] КРИТИЧЕСКАЯ ОШИБКА: {e}', flush=True)
        traceback.print_exc()

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main()) 
