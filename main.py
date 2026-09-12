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

api_id = 37868561
api_hash = 'c572dbecd109072ab7ef2935d265b0d8'
session_string = os.environ.get('SESSION_STRING', '')
MRKT_TOKEN = os.environ.get('MRKT_TOKEN', '')

client = TelegramClient(StringSession(session_string), api_id, api_hash)

GROUP_ID = -1004329127019
TOPICS = {
    'до 3': 3, '3-10': 5, '10-30': 7, '30-50': 9,
    '50-100': 11, '100-500': 13, '500+': 19,
}
WATCH_ACCOUNTS = ['mrktbank', 'giftstoportals']
PRICE_BOT = 'PriceNFTbot'

seen_gift_slugs = set()
pending_price_requests = {}
initialized = False  # Флаг: первый запуск уже был

app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return 'OK', 200

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def extract_price(text):
    """Расширенный парсер цены — ловит Floor, Price, Флор, просто число TON."""
    if not text:
        return None
    patterns = [
        r'Floor[:\s]+([\d.,]+)\s*(?:TON|GRAM|⭐|USDT)?',
        r'Флор[:\s]+([\d.,]+)\s*(?:TON|GRAM|⭐|USDT)?',
        r'Price[:\s]+([\d.,]+)\s*(?:TON|GRAM|⭐|USDT)?',
        r'Цена[:\s]+([\d.,]+)\s*(?:TON|GRAM|⭐|USDT)?',
        r'([\d.,]+)\s*(?:TON|GRAM)',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(',', '.'))
            except ValueError:
                continue
    return None

def get_thread_id(price):
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
    thread_id = get_thread_id(price)
    if thread_id is None:
        print(f'[!] Нет темы для цены {price}', flush=True)
        return
    try:
        await client.send_message(GROUP_ID, notification, reply_to=thread_id)
        print(f'[+] Отправлено в тему {thread_id}', flush=True)
    except Exception as e:
        print(f'[!] Ошибка отправки: {e}', flush=True)

@client.on(events.NewMessage(chats=PRICE_BOT))
async def price_bot_handler(event):
    if event.out:
        return
    if event.message.reply_to_msg_id in pending_price_requests:
        gift_slug = pending_price_requests.pop(event.message.reply_to_msg_id)
        text = event.message.text
        if not text:
            print(f'[!] Пустой ответ для {gift_slug}', flush=True)
            return
        print(f'[PRICE_BOT] Ответ для {gift_slug}: {text[:200]}', flush=True)
        price = extract_price(text)
        if price is None:
            print(f'[!] Цена не найдена в ответе для {gift_slug}', flush=True)
            return
        gift_link = f'https://t.me/nft/{gift_slug}'
        notification = (
            f'📊 Новый подарок: {gift_link}\n'
            f'💰 Флор: {price} TON\n'
            f'🕒 Обновлено'
        )
        await send_to_group(notification, price)

def extract_slug_from_gift(gift):
    """Пытаемся вытащить slug подарка из SavedStarGift."""
    # 1. Прямое поле slug
    if hasattr(gift, 'slug') and gift.slug:
        return gift.slug
    # 2. Вложенный gift (StarGift)
    if hasattr(gift, 'gift') and gift.gift:
        inner = gift.gift
        if hasattr(inner, 'slug') and inner.slug:
            return inner.slug
    # 3. Собираем из title + gift_num
    title = None
    if hasattr(gift, 'gift') and gift.gift and hasattr(gift.gift, 'title'):
        title = gift.gift.title
    num = getattr(gift, 'gift_num', None)
    if title and num:
        return f'{title}-{num}'
    return None

async def initialize_seen():
    """Заполняем seen_gift_slugs ВСЕМИ текущими подарками, без отправки в PriceNFTbot."""
    global initialized
    print('[INIT] Загружаю существующие подарки в память...', flush=True)
    total = 0
    for username in WATCH_ACCOUNTS:
        try:
            offset = ''
            while True:
                result = await client(functions.payments.GetSavedStarGiftsRequest(
                    peer=username,
                    offset=offset,
                    limit=100
                ))
                for gift in result.gifts:
                    slug = extract_slug_from_gift(gift)
                    if slug:
                        seen_gift_slugs.add(slug)
                        total += 1
                if not result.next_offset:
                    break
                offset = result.next_offset
                await asyncio.sleep(1)
            print(f'[INIT] @{username}: загружено', flush=True)
        except Exception as e:
            print(f'[INIT] Ошибка @{username}: {e}', flush=True)
    print(f'[INIT] Всего запомнено подарков: {len(seen_gift_slugs)}', flush=True)
    initialized = True

async def check_profile(username):
    """Проверяем новые подарки. При первом запуске — только запоминаем."""
    if not initialized:
        return
    try:
        offset = ''
        new_gifts = []
        while True:
            result = await client(functions.payments.GetSavedStarGiftsRequest(
                peer=username,
                offset=offset,
                limit=100
            ))
            for gift in result.gifts:
                slug = extract_slug_from_gift(gift)
                if slug and slug not in seen_gift_slugs:
                    seen_gift_slugs.add(slug)
                    new_gifts.append(slug)
            if not result.next_offset:
                break
            offset = result.next_offset
            await asyncio.sleep(1)

        if new_gifts:
            print(f'[+] @{username}: найдено {len(new_gifts)} новых', flush=True)
            for slug in new_gifts:
                try:
                    sent_msg = await client.send_message(PRICE_BOT, f'https://t.me/nft/{slug}')
                    pending_price_requests[sent_msg.id] = slug
                    print(f'[PRICE_BOT] Запрос для {slug}', flush=True)
                    await asyncio.sleep(4)  # Пауза 4 сек между запросами
                except Exception as e:
                    print(f'[!] Ошибка отправки в PriceNFTbot: {e}', flush=True)
                    await asyncio.sleep(10)  # Если ошибка — пауза больше
        else:
            print(f'[=] @{username}: новых нет', flush=True)
    except Exception as e:
        print(f'[!] Ошибка @{username}: {e}', flush=True)

async def profile_monitor():
    # Сначала инициализация — запоминаем всё существующее
    await initialize_seen()
    # Потом начинаем следить за новыми
    while True:
        for username in WATCH_ACCOUNTS:
            await check_profile(username)
        await asyncio.sleep(120)

async def main():
    try:
        print('[MAIN] Подключаюсь...', flush=True)
        await client.start()
        me = await client.get_me()
        print(f'[+] Подключён как: @{me.username}', flush=True)
        try:
            group = await client.get_entity(GROUP_ID)
            print(f'[+] Группа: {group.title}', flush=True)
        except Exception as e:
            print(f'[!] Группа: {e}', flush=True)

        asyncio.create_task(profile_monitor())
        print('🚀 Парсер запущен', flush=True)

        await client.run_until_disconnected()
    except Exception as e:
        print(f'[!!!] КРИТИЧЕСКАЯ: {e}', flush=True)
        traceback.print_exc()

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main()) 
