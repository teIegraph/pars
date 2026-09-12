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

MAX_GIFTS_PER_CHECK = 100

seen_gift_slugs = set()
# Для каждого отправленного сообщения храним slug и ждём ОТВЕТ С КНОПКАМИ
pending_price_requests = {}  # {msg_id: gift_slug}
# После нажатия кнопки — ждём ответа с ценой
pending_price_reply = {}  # {msg_id_after_click: gift_slug}

initialized = False

app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return 'OK', 200

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def extract_price(text):
    if not text:
        return None
    patterns = [
        r'Floor[:\s]+([\d.,]+)',
        r'Флор[:\s]+([\d.,]+)',
        r'Price[:\s]+([\d.,]+)',
        r'Цена[:\s]+([\d.,]+)',
        r'([\d.,]+)\s*(?:TON|GRAM|💎)',
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

    msg_id = event.message.id
    text = event.message.text or ''
    buttons = event.message.buttons  # список списков кнопок

    # === СЛУЧАЙ 1: ответ с кнопками (после нашего запроса) ===
    if msg_id in pending_price_requests and buttons:
        gift_slug = pending_price_requests.pop(msg_id)
        print(f'[PRICE_BOT] Получены кнопки для {gift_slug}, нажимаю «Информация о подарке»', flush=True)
        # Ищем кнопку "Информация о подарке"
        target_button = None
        for row in buttons:
            for btn in row:
                btn_text = (btn.text or '').lower()
                if 'информация' in btn_text and 'подар' in btn_text:
                    target_button = btn
                    break
            if target_button:
                break
        if not target_button:
            print(f'[!] Кнопка «Информация о подарке» не найдена для {gift_slug}', flush=True)
            return
        try:
            # Кликаем и ждём ответ с ценой
            reply = await target_button.click()
            # reply — это сообщение, которое бот пришлёт в ответ
            if reply and hasattr(reply, 'text') and reply.text:
                price = extract_price(reply.text)
                if price:
                    gift_link = f'https://t.me/nft/{gift_slug}'
                    notification = (
                        f'📊 Новый подарок: {gift_link}\n'
                        f'💰 Флор: {price} TON\n'
                        f'🕒 Обновлено'
                    )
                    await send_to_group(notification, price)
                else:
                    print(f'[!] Цена не найдена в ответе: {reply.text[:300]}', flush=True)
            else:
                # Если reply не пришёл сразу — сохраним связку, чтобы поймать следующее сообщение
                print(f'[?] Ждём отдельного сообщения с ценой для {gift_slug}', flush=True)
                pending_price_reply[msg_id + 1] = gift_slug  # грубая привязка
        except Exception as e:
            print(f'[!] Ошибка клика по кнопке: {e}', flush=True)
        return

    # === СЛУЧАЙ 2: пришло отдельное сообщение с ценой (без кнопок) ===
    if msg_id in pending_price_reply:
        gift_slug = pending_price_reply.pop(msg_id)
        price = extract_price(text)
        if price:
            gift_link = f'https://t.me/nft/{gift_slug}'
            notification = (
                f'📊 Новый подарок: {gift_link}\n'
                f'💰 Флор: {price} TON\n'
                f'🕒 Обновлено'
            )
            await send_to_group(notification, price)
        else:
            print(f'[!] Цена всё ещё не найдена для {gift_slug}: {text[:200]}', flush=True)
        return

    # === СЛУЧАЙ 3: первый ответ бота (без кнопок) — например, "подождите" ===
    if text:
        print(f'[PRICE_BOT] Сообщение: {text[:200]}', flush=True)

def extract_slug_from_gift(gift):
    if hasattr(gift, 'slug') and gift.slug:
        return gift.slug
    if hasattr(gift, 'gift') and gift.gift:
        inner = gift.gift
        if hasattr(inner, 'slug') and inner.slug:
            return inner.slug
    title = None
    if hasattr(gift, 'gift') and gift.gift and hasattr(gift.gift, 'title'):
        title = gift.gift.title
    num = getattr(gift, 'gift_num', None)
    if title and num:
        return f'{title}-{num}'
    return None

async def fetch_recent_gifts(username, limit=MAX_GIFTS_PER_CHECK):
    result = await client(functions.payments.GetSavedStarGiftsRequest(
        peer=username,
        offset='',
        limit=limit
    ))
    return result.gifts

async def initialize_seen():
    global initialized
    print(f'[INIT] Загружаю последние {MAX_GIFTS_PER_CHECK} подарков...', flush=True)
    for username in WATCH_ACCOUNTS:
        try:
            gifts = await fetch_recent_gifts(username)
            count = 0
            for gift in gifts:
                slug = extract_slug_from_gift(gift)
                if slug:
                    seen_gift_slugs.add(slug)
                    count += 1
            print(f'[INIT] @{username}: запомнено {count} (из {len(gifts)})', flush=True)
            await asyncio.sleep(2)
        except Exception as e:
            print(f'[INIT] Ошибка @{username}: {e}', flush=True)
    print(f'[INIT] Готово. Всего в памяти: {len(seen_gift_slugs)}', flush=True)
    initialized = True

async def check_profile(username):
    if not initialized:
        return
    try:
        gifts = await fetch_recent_gifts(username)
        new_gifts = []
        for gift in gifts:
            slug = extract_slug_from_gift(gift)
            if slug and slug not in seen_gift_slugs:
                seen_gift_slugs.add(slug)
                new_gifts.append(slug)

        if new_gifts:
            print(f'[+] @{username}: найдено {len(new_gifts)} новых', flush=True)
            for slug in new_gifts:
                try:
                    sent_msg = await client.send_message(PRICE_BOT, f'https://t.me/nft/{slug}')
                    pending_price_requests[sent_msg.id] = slug
                    print(f'[PRICE_BOT] Запрос для {slug}', flush=True)
                    await asyncio.sleep(6)  # Пауза между запросами
                except Exception as e:
                    print(f'[!] Ошибка отправки в PriceNFTbot: {e}', flush=True)
                    await asyncio.sleep(15)
        else:
            print(f'[=] @{username}: новых нет', flush=True)
    except Exception as e:
        print(f'[!] Ошибка @{username}: {e}', flush=True)

async def profile_monitor():
    await initialize_seen()
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
