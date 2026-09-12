from telethon import TelegramClient, events
from telethon.sessions import StringSession
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

# Группа и темы для логов
GROUP_ID = -1004329127019
TOPICS = {
    'до 3': 3,
    '3-10': 5,
    '10-30': 7,
    '30-50': 9,
    '50-100': 11,
    '100-500': 13,
    '500+': 19,
}

# Аккаунты, за которыми следим
WATCH_ACCOUNTS = ['mrktbank', 'giftstoportals']
# Бот для оценки цен
PRICE_BOT = 'PriceNFTbot'

# Словарь для хранения ссылок, которые мы отправили боту, и ID сообщений
pending_links = {}
last_price_bot_msg_id = 0

app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return 'OK', 200

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def extract_gift_link(text):
    """Ищем ссылку на подарок в тексте."""
    m = re.search(r'(?:t\.me/nft/|https?://t\.me/nft/)([A-Za-z0-9_]+-\d+)', text, re.IGNORECASE)
    if m:
        return m.group(1)
    return None

def extract_price(text):
    """Ищем цену в ответе бота."""
    # Ищем "Floor: 4.35 TON" или "Floor: 4.35 ⭐"
    m = re.search(r'Floor[:\s]+([\d.,]+)\s*(?:⭐|TON|GRAM|USDT)?', text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(',', '.')), 'TON'  # Предполагаем TON, если не указано иное
        except ValueError:
            pass
    # Ищем просто цену с TON/GRAM
    m = re.search(r'([\d.,]+)\s*(TON|GRAM|USDT)', text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1).replace(',', '.')), m.group(2).upper()
        except ValueError:
            pass
    return None, None

def get_thread_id(price):
    """Определяем ID темы по цене."""
    if price is None:
        return None
    if price < 3:
        return TOPICS['до 3']
    elif price < 10:
        return TOPICS['3-10']
    elif price < 30:
        return TOPICS['10-30']
    elif price < 50:
        return TOPICS['30-50']
    elif price < 100:
        return TOPICS['50-100']
    elif price < 500:
        return TOPICS['100-500']
    else:
        return TOPICS['500+']

@client.on(events.NewMessage(chats=WATCH_ACCOUNTS))
async def watch_handler(event):
    """Слушаем новые сообщения от @mrktbank и @giftstoportals."""
    text = event.message.text
    if not text:
        return
    
    print(f'[НОВОЕ] @{event.chat.username}: {text[:200]}', flush=True)
    
    gift_link = extract_gift_link(text)
    if gift_link:
        print(f'[ССЫЛКА] Найден подарок: {gift_link}', flush=True)
        # Отправляем ссылку боту @PriceNFTbot
        try:
            sent_msg = await client.send_message(PRICE_BOT, gift_link)
            pending_links[sent_msg.id] = gift_link
            print(f'[PRICE_BOT] Отправлен запрос для {gift_link}', flush=True)
        except Exception as e:
            print(f'❌ Ошибка при отправке в @{PRICE_BOT}: {e}', flush=True)
    else:
        print(f'[ССЫЛКА] Ссылка на подарок не найдена', flush=True)

@client.on(events.NewMessage(chats=PRICE_BOT))
async def price_bot_handler(event):
    """Слушаем ответы от @PriceNFTbot."""
    global last_price_bot_msg_id
    # Игнорируем сообщения, которые мы сами отправили
    if event.out:
        return
    
    # Проверяем, является ли это ответом на наш запрос
    if event.message.reply_to_msg_id in pending_links:
        gift_link = pending_links.pop(event.message.reply_to_msg_id)
        text = event.message.text
        if not text:
            return
        
        print(f'[PRICE_BOT] Ответ для {gift_link}: {text[:200]}', flush=True)
        
        price, currency = extract_price(text)
        if price is None:
            print(f'[PRICE_BOT] Не удалось определить цену для {gift_link}', flush=True)
            return
        
        thread_id = get_thread_id(price)
        if thread_id is None:
            print(f'[PRICE_BOT] Нет темы для цены {price} {currency}', flush=True)
            return
        
        notification = (
            f'📊 ФЛОР: {gift_link}\n'
            f'💰 Цена: {price} {currency}\n'
            f'📢 Источник: @PriceNFTbot\n'
            f'🕒 Обновлено'
        )
        
        try:
            await client.send_message(GROUP_ID, notification, reply_to=thread_id)
            print(f'📩 Отправлено в тему {thread_id}: {gift_link} ({price} {currency})', flush=True)
        except Exception as e:
            print(f'❌ Ошибка при отправке в группу: {e}', flush=True)

async def main():
    try:
        print('[MAIN] Подключаюсь к Telegram...', flush=True)
        await client.start()
        me = await client.get_me()
        print(f'✅ Подключён как: @{me.username}', flush=True)
        
        try:
            group = await client.get_entity(GROUP_ID)
            print(f'✅ Группа найдена: {group.title}', flush=True)
        except Exception as e:
            print(f'⚠️ Не могу получить группу {GROUP_ID}: {e}', flush=True)
        
        for acc in WATCH_ACCOUNTS:
            try:
                await client.get_entity('@' + acc)
                print(f'✅ Подписан на @{acc}', flush=True)
            except Exception as e:
                print(f'❌ Ошибка с @{acc}: {e}', flush=True)
        
        print('🚀 Парсер флора запущен. Ждём новые подарки...', flush=True)
        
        await client.run_until_disconnected()
        
    except Exception as e:
        print(f'❌❌❌ КРИТИЧЕСКАЯ ОШИБКА: {e}', flush=True)
        traceback.print_exc()

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())
