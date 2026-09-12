from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl import functions, types
import asyncio
import re
from flask import Flask
import threading
import os
import traceback

# --- НАСТРОЙКИ ---
api_id = 37868561
api_hash = 'c572dbecd109072ab7ef2935d265b0d8'
session_string = os.environ.get('SESSION_STRING', '')

client = TelegramClient(StringSession(session_string), api_id, api_hash)

GROUP_ID = -1004329127019
TOPICS = {
    'до 3': 3, '3-10': 5, '10-30': 7, '30-50': 9,
    '50-100': 11, '100-500': 13, '500+': 19,
}

seen_listings = set()  # Для отслеживания уже виденных лотов на маркете

app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return 'OK', 200

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def extract_price(text):
    """Извлекаем цену (Floor или AVG для фона)."""
    if not text: return None
    avg_matches = re.findall(r'AVG:\s*([\d.,]+)', text, re.IGNORECASE)
    floor_match = re.search(r'Floor:\s*([\d.,]+)', text, re.IGNORECASE)
    floor_price = float(floor_match.group(1).replace(',', '.')) if floor_match else None
    
    if len(avg_matches) >= 2:
        try: return float(avg_matches[-1].replace(',', '.'))
        except ValueError: pass
    if floor_price is not None: return floor_price
    if len(avg_matches) == 1:
        try: return float(avg_matches[0].replace(',', '.'))
        except ValueError: pass
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

async def send_to_group(notification, price):
    thread_id = get_thread_id(price)
    if thread_id is None: return
    try:
        await client.send_message(GROUP_ID, notification, reply_to=thread_id)
        print(f'[+] Отправлено в тему {thread_id}', flush=True)
    except Exception as e:
        print(f'[!] Ошибка отправки: {e}', flush=True)

# --- 1. ОТСЛЕЖИВАНИЕ АПГРЕЙДОВ ---
@client.on(events.NewMessage())
async def upgrade_handler(event):
    """Ловим сервисные сообщения об апгрейде."""
    if not event.message.action: return
    action = event.message.action
    
    # Проверяем, что это апгрейд подарка
    if isinstance(action, types.MessageActionStarGiftUnique):
        try:
            gift = action.gift  # Объект StarGift
            slug = gift.slug
            title = gift.title
            print(f'[UPGRADE] {title} (#{slug})', flush=True)
            
            # Отправляем ссылку в PriceNFTbot для оценки
            sent_msg = await client.send_message('PriceNFTbot', f'https://t.me/nft/{slug}')
            # Ждём ответ (упрощённо: PriceNFTbot пришлёт сообщение, мы его поймаем)
        except Exception as e:
            print(f'[!] Ошибка обработки апгрейда: {e}', flush=True)

# --- 2. ОТСЛЕЖИВАНИЕ ЛОТОВ НА МАРКЕТЕ ---
async def check_market_listings():
    """Получаем лоты с внутреннего маркета."""
    try:
        result = await client(functions.payments.GetResaleStarGiftsRequest(
            offset='', limit=50
        ))
        for gift in result.gifts:
            # Уникальный ID лота
            listing_id = f"{gift.gift_id}_{gift.resale_amount}"
            if listing_id in seen_listings: continue
            seen_listings.add(listing_id)
            
            slug = gift.gift.slug if hasattr(gift, 'gift') else None
            price = gift.resale_amount / 1_000_000  # Переводим из нанотонов
            if slug and price:
                notification = (
                    f'🛒 Новый лот на маркете: https://t.me/nft/{slug}\n'
                    f'💰 Цена: {price} TON'
                )
                await send_to_group(notification, price)
    except Exception as e:
        print(f'[!] Ошибка маркета: {e}', flush=True)

async def market_monitor():
    while True:
        await check_market_listings()
        await asyncio.sleep(30)  # Проверяем каждые 30 секунд

# --- ЗАПУСК ---
async def main():
    try:
        print('[MAIN] Подключаюсь...', flush=True)
        await client.start()
        me = await client.get_me()
        print(f'[+] Подключён: @{me.username}', flush=True)
        
        asyncio.create_task(market_monitor())
        print('🚀 Парсер запущен (апгрейды + маркет)', flush=True)
        await client.run_until_disconnected()
    except Exception as e:
        print(f'[!!!] КРИТИЧЕСКАЯ: {e}', flush=True)
        traceback.print_exc()

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())
