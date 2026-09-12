from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl import functions, types
import asyncio
import re
from flask import Flask
import threading
import os
import traceback

api_id = 37868561
api_hash = 'c572dbecd109072ab7ef2935d265b0d8'
session_string = os.environ.get('SESSION_STRING', '')

client = TelegramClient(StringSession(session_string), api_id, api_hash)

GROUP_ID = -1004329127019
TOPICS = {
    'до 3': 3, '3-10': 5, '10-30': 7, '30-50': 9,
    '50-100': 11, '100-500': 13, '500+': 19,
}

MAX_NEW_LISTINGS = 50           # Сколько новых лотов собираем за один проход
CHECK_INTERVAL = 120            # Проверка раз в 2 минуты
COLLECTION_REFRESH = 600        # Список коллекций обновляем раз в 10 минут

seen_listing_ids = set()
pending_buttons = {}
pending_price = {}

app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return 'OK', 200

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def extract_price(text):
    if not text: return None
    avg = re.findall(r'AVG:\s*([\d.,]+)', text, re.IGNORECASE)
    floor_m = re.search(r'Floor:\s*([\d.,]+)', text, re.IGNORECASE)
    floor = None
    if floor_m:
        try: floor = float(floor_m.group(1).replace(',', '.'))
        except: pass
    if len(avg) >= 2:
        try: return float(avg[-1].replace(',', '.'))
        except: pass
    if floor is not None: return floor
    if len(avg) == 1:
        try: return float(avg[0].replace(',', '.'))
        except: pass
    return None

def get_thread_id(price):
    if price is None: return None
    if price < 3: return TOPICS['до 3']
    elif price < 10: return TOPICS['3-10']
    elif price < 30: return TOPICS['10-30']
    elif price < 50: return TOPICS['30-50']
    elif price < 100: return TOPICS['50-100']
    elif price < 500: return TOPICS['100-500']
    else: return TOPICS['500+']

async def send_to_group(text, price):
    thread_id = get_thread_id(price)
    if thread_id is None:
        print(f'[!] Нет темы для {price}', flush=True)
        return
    try:
        await client.send_message(GROUP_ID, text, reply_to=thread_id)
        print(f'[+] Отправлено в тему {thread_id}: {text[:80]}', flush=True)
    except Exception as e:
        print(f'[!] Ошибка отправки: {e}', flush=True)

# --- АПГРЕЙДЫ ---
@client.on(events.NewMessage())
async def upgrade_watcher(event):
    action = getattr(event.message, 'action', None)
    if not action: return
    if isinstance(action, types.MessageActionStarGiftUnique):
        try:
            gift = action.gift
            slug = getattr(gift, 'slug', None)
            if not slug: return
            print(f'[UPGRADE] {slug}', flush=True)
            sent = await client.send_message('PriceNFTbot', f'https://t.me/nft/{slug}')
            pending_buttons[sent.id] = slug
        except Exception as e:
            print(f'[!] Апгрейд ошибка: {e}', flush=True)

# --- PRICENFTBOT: НАЖИМАЕМ КНОПКУ И ЛОВИМ ЦЕНУ ---
@client.on(events.NewMessage(chats='PriceNFTbot'))
async def price_bot_handler(event):
    if event.out: return
    reply_to = event.message.reply_to_msg_id
    text = event.message.text or ''
    buttons = event.message.buttons

    if reply_to and reply_to in pending_buttons and buttons:
        gift_slug = pending_buttons.pop(reply_to)
        target = None
        for row in buttons:
            for btn in row:
                t = (btn.text or '').lower()
                if ('gift' in t and 'info' in t) or ('информация' in t and 'подар' in t):
                    target = btn; break
            if target: break
        if not target:
            print(f'[!] Кнопка не найдена: {[b.text for row in buttons for b in row]}', flush=True)
            return
        await target.click()
        pending_price[reply_to] = gift_slug
        return

    if reply_to and reply_to in pending_price:
        gift_slug = pending_price.pop(reply_to)
        price = extract_price(text)
        if price is None:
            print(f'[!] Цена не найдена: {text[:200]}', flush=True)
            return
        await send_to_group(
            f'⬆️ Апгрейд: https://t.me/nft/{gift_slug}\n💰 Флор: {price} TON',
            price
        )

# --- ЛОТЫ С TG-МАРКЕТА ---
async def get_collections():
    try:
        result = await client(functions.payments.GetStarGiftsRequest(hash=0))
        return result.gifts
    except Exception as e:
        print(f'[!] Коллекции: {e}', flush=True)
        return []

async def fetch_latest_from_collection(coll):
    """Берём 1 самый свежий лот из коллекции."""
    try:
        cid = getattr(coll, 'id', None)
        title = getattr(coll, 'title', '?')
        if not cid: return None
        res = await client(functions.payments.GetResaleStarGiftsRequest(
            gift_id=cid, offset='', limit=1
        ))
        if not res.gifts: return None
        g = res.gifts[0]
        num = getattr(g, 'gift_num', 0)
        nano = getattr(g, 'resale_amount', 0)
        if not nano: return None
        price = nano / 1_000_000_000  # nanoTON -> TON
        uid = f'{cid}_{num}_{nano}'
        slug = f'{title.replace(" ", "")}-{num}'
        return uid, slug, price
    except Exception:
        return None

async def market_monitor():
    collections = []
    counter = 0
    while True:
        try:
            if counter % 5 == 0:
                collections = await get_collections()
                print(f'[MARKET] Коллекций: {len(collections)}', flush=True)

            new_count = 0
            for coll in collections:
                if new_count >= MAX_NEW_LISTINGS:
                    break
                item = await fetch_latest_from_collection(coll)
                await asyncio.sleep(0.4)  # пауза, чтобы не забанили
                if not item:
                    continue
                uid, slug, price = item
                if uid in seen_listing_ids:
                    continue
                seen_listing_ids.add(uid)
                new_count += 1
                await send_to_group(
                    f'🛒 Новый лот: https://t.me/nft/{slug}\n💰 Цена: {price} TON',
                    price
                )

            print(f'[MARKET] Новых лотов обработано: {new_count}', flush=True)
            counter += 1
        except Exception as e:
            print(f'[!] MARKET ошибка: {e}', flush=True)
        await asyncio.sleep(CHECK_INTERVAL)

async def main():
    try:
        print('[MAIN] Подключаюсь...', flush=True)
        await client.start()
        me = await client.get_me()
        print(f'[+] Подключён: @{me.username}', flush=True)
        try:
            group = await client.get_entity(GROUP_ID)
            print(f'[+] Группа: {group.title}', flush=True)
        except Exception as e:
            print(f'[!] Группа: {e}', flush=True)

        asyncio.create_task(market_monitor())
        print('🚀 Парсер запущен (TG-маркет + апгрейды)', flush=True)
        await client.run_until_disconnected()
    except Exception as e:
        print(f'[!!!] КРИТИЧЕСКАЯ: {e}', flush=True)
        traceback.print_exc()

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())
