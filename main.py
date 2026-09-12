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

client = TelegramClient(StringSession(session_string), api_id, api_hash)

# Каналы-источники
TARGETS = ['mrktnotification', 'portals_notifications', 'mrktbank', 'giftstoportals']
KEYWORDS = ['upgrade', 'улучшен', 'upgraded', 'new listing', 'выставлен', 'listed', 'sold', 'продан', 'gift', 'подарок', 'nft']

# Группа и темы
GROUP_ID = -1004329127019  # ID группы «логи»
TOPICS = {
    'до 3': 3,
    '3-10': 5,
    '10-30': 7,
    '30-50': 9,
    '50-100': 11,
    '100-500': 13,
    '500+': 19,
}

last_ids = {}

app = Flask(__name__)

@app.route('/')
@app.route('/health')
def health():
    return 'OK', 200

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def extract_price(text):
    """Ищем цену в разных форматах."""
    patterns = [
        r'Price[:\s]+([\d.,]+)\s*\**\s*(TON|GRAM|USDT|BTC|ETH)',
        r'Sold for[:\s]+([\d.,]+)\s*\**\s*(TON|GRAM|USDT|BTC|ETH)',
        r'💰\s*([\d.,]+)\s*\**\s*(TON|GRAM|USDT|BTC|ETH)',
        r'([\d.,]+)\s*\**\s*(TON|GRAM|USDT|BTC|ETH)',
        r'([\d.,]+)\s*(TON|GRAM|USDT|BTC|ETH)',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            price_str = m.group(1).replace(',', '.')
            try:
                return float(price_str), m.group(2).upper()
            except ValueError:
                continue
    return None, None

def get_thread_id(price, currency):
    """Определяем thread_id по цене (только для TON)."""
    if price is None:
        return None
    if currency not in ('TON', 'GRAM'):
        return None
    # GRAM примерно = TON (упрощение)
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

async def main():
    try:
        print('[MAIN] Подключаюсь к Telegram...', flush=True)
        await client.start()
        me = await client.get_me()
        print(f'✅ Подключён как: @{me.username} (id: {me.id})', flush=True)

        # Проверяем доступ к группе
        try:
            group = await client.get_entity(GROUP_ID)
            print(f'✅ Группа найдена: {group.title}', flush=True)
        except Exception as e:
            print(f'⚠️ Не могу получить группу {GROUP_ID}: {e}', flush=True)

        for t in TARGETS:
            try:
                entity = await client.get_entity('@' + t)
                last = await client.get_messages(entity, limit=1)
                last_ids[t] = last[0].id if last else 0
                print(f'✅ Подписан на @{t}', flush=True)
            except Exception as e:
                print(f'❌ Ошибка с @{t}: {e}', flush=True)

        print('🚀 Парсер запущен.', flush=True)
        while True:
            for t in TARGETS:
                try:
                    entity = await client.get_entity('@' + t)
                    messages = await client.get_messages(entity, limit=5)
                    for msg in messages:
                        if msg.id > last_ids.get(t, 0):
                            last_ids[t] = msg.id
                            if msg.text:
                                await process_message(msg.text, t)
                except Exception as e:
                    print(f'Ошибка при чтении @{t}: {e}', flush=True)
            await asyncio.sleep(20)
    except Exception as e:
        print(f'❌❌❌ КРИТИЧЕСКАЯ ОШИБКА: {e}', flush=True)
        traceback.print_exc()

async def process_message(text, source):
    if not any(kw.lower() in text.lower() for kw in KEYWORDS):
        return

    price, currency = extract_price(text)
    thread_id = get_thread_id(price, currency)

    price_str = f'{price} {currency}' if price else 'не указана'

    notification = (
        f'🎁 НОВОЕ СОБЫТИЕ!\n'
        f'📢 Источник: @{source}\n'
        f'💰 Цена: {price_str}\n'
        f'📄 Текст:\n{text[:400]}'
    )

    print(f'\n[{source}] {price_str}\n{text[:150]}', flush=True)

    # Отправляем в нужную тему
    if thread_id is None:
        print(f'⚠️ Цена не определена — уведомление не отправлено в группу', flush=True)
        return

    try:
        await client.send_message(GROUP_ID, notification, reply_to=thread_id)
        print(f'📩 Отправлено в тему {thread_id}', flush=True)
    except Exception as e:
        print(f'⚠️ Не удалось отправить: {e}', flush=True)

if __name__ == '__main__':
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(main())
