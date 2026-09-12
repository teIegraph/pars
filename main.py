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
seen_mrkt_gifts = set()
pending_price_requests = {}

app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return 'OK', 200

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def extract_price(text):
    m = re.search(r'Floor[:\s]+([\d.,]+)\s*(?:TON|GRAM|⭐|USDT)?', text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(',', '.'))
        except ValueError:
            pass
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
            return
        price = extract_price(text)
        if price is None:
            print(f'[!] Цена не определена для {gift_slug}', flush=True)
            return
        gift_link = f'https://t.me/nft/{gift_slug}'
        notification = (
            f'📊 Новый подарок: {gift_link}\n'
            f'💰 Флор: {price} TON\n'
            f'🕒 Обновлено'
        )
        await send_to_group(notification, price)

async def debug_check(username):
    """Подробная проверка одного профиля — вывод в лог."""
    print(f'\n=== DEBUG @{username} ===', flush=True)
    try:
        result = await client(functions.payments.GetSavedStarGiftsRequest(
            peer=username,
            offset='',
            limit=10
        ))
        print(f'Всего подарков: {len(result.gifts)}', flush=True)
        for i, gift in enumerate(result.gifts[:5]):
            attrs = [a for a in dir(gift) if not a.startswith('_')]
            print(f'  [{i}] type={type(gift).__name__}', flush=True)
            print(f'      attrs: {attrs[:15]}', flush=True)
            if hasattr(gift, 'slug'):
                print(f'      slug={gift.slug}', flush=True)
    except Exception as e:
        print(f'ОШИБКА: {e}', flush=True)

async def check_profile(username):
    try:
        result = await client(functions.payments.GetSavedStarGiftsRequest(
            peer=username,
            offset='',
            limit=100
        ))
        new_gifts = []
        for gift in result.gifts:
            if hasattr(gift, 'slug') and gift.slug and gift.slug not in seen_gift_slugs:
                seen_gift_slugs.add(gift.slug)
                new_gifts.append(gift.slug)
        if new_gifts:
            print(f'[+] @{username}: найдено {len(new_gifts)}', flush=True)
            for slug in new_gifts:
                sent_msg = await client.send_message(PRICE_BOT, f'https://t.me/nft/{slug}')
                pending_price_requests[sent_msg.id] = slug
        else:
            print(f'[=] @{username}: новых нет', flush=True)
    except Exception as e:
        print(f'[!] Ошибка @{username}: {e}', flush=True)

async def profile_monitor():
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

        # DEBUG-проверка при старте
        for username in WATCH_ACCOUNTS:
            await debug_check(username)

        asyncio.create_task(profile_monitor())
        print('🚀 Парсер запущен', flush=True)

        await client.run_until_disconnected()
    except Exception as e:
        print(f'[!!!] КРИТИЧЕСКАЯ: {e}', flush=True)
        traceback.print_exc()

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main()) 
