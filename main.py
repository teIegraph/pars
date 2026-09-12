from telethon import TelegramClient
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

print(f'[START] Длина сессии: {len(session_string)}', flush=True)
print(f'[START] Первые 20 символов: {session_string[:20]}...', flush=True)

client = TelegramClient(StringSession(session_string), api_id, api_hash)

TARGETS = ['mrktnotification', 'portals_notifications', 'mrktbank', 'giftstoportals']
KEYWORDS = ['upgrade', 'улучшен', 'upgraded', 'new listing', 'выставлен', 'listed', 'sold', 'продан', 'gift', 'подарок', 'nft']
last_ids = {}

app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return 'OK', 200

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

async def main():
    try:
        print('[MAIN] Начинаю подключение к Telegram...', flush=True)
        await client.start()
        me = await client.get_me()
        print(f'✅ Подключён как: @{me.username} (id: {me.id})', flush=True)

        for t in TARGETS:
            try:
                entity = await client.get_entity('@' + t)
                last = await client.get_messages(entity, limit=1)
                last_ids[t] = last[0].id if last else 0
                print(f'✅ Подписан на @{t}', flush=True)
            except Exception as e:
                print(f'❌ Ошибка с @{t}: {e}', flush=True)

        print('🚀 Парсер запущен. Слежу за новыми лотами...', flush=True)
        while True:
            for t in TARGETS:
                try:
                    entity = await client.get_entity('@' + t)
                    messages = await client.get_messages(entity, limit=5)
                    for msg in messages:
                        if msg.id > last_ids.get(t, 0):
                            last_ids[t] = msg.id
                            if msg.text:
                                process_message(msg.text, t)
                except Exception as e:
                    print(f'Ошибка при чтении @{t}: {e}', flush=True)
            await asyncio.sleep(20)
    except Exception as e:
        print(f'❌❌❌ КРИТИЧЕСКАЯ ОШИБКА: {e}', flush=True)
        traceback.print_exc()

def process_message(text, source):
    if not any(kw.lower() in text.lower() for kw in KEYWORDS):
        return
    price_match = re.search(r'([\d.]+)\s*\**\s*(TON|GRAM|USDT|BTC|ETH)', text, re.IGNORECASE)
    price = price_match.group(1) if price_match else '?'
    currency = price_match.group(2).upper() if price_match else ''
    print(f'\n🎁 НОВОЕ СОБЫТИЕ!', flush=True)
    print(f'📢 Источник: @{source}', flush=True)
    print(f'💰 Цена: {price} {currency}', flush=True)
    print(f'📄 Текст: {text[:250]}...', flush=True)
    print('-' * 40, flush=True)

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())
