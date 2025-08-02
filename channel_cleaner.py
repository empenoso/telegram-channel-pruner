# Удаляем незваных подписчиков из своего телеграм канала

# Для запуска (под Windows):
# 1. Получение информации о канале:
#    python channel_cleaner.py info
# 2. Сохранение "белого списка" (если нужно, но для каналов неполный):
#    python channel_cleaner.py save
# 3. ГЛАВНАЯ КОМАНДА: Удаление подписчиков, присоединившихся после указанной даты.
#    Время последнего "хорошего" подписчика или время начала атаки ботов.
#    python channel_cleaner.py kickbydate --after-date "2025-08-01 10:40:00"
#    python channel_cleaner.py kickbydate --after-date "2025-08-01 10:40:00" --yes (без подтверждения)

import os
import csv
import time
import asyncio
import sys
import argparse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from dotenv import load_dotenv

from telethon.sync import TelegramClient
from telethon.tl.functions.channels import GetParticipantsRequest

# Используем правильный фильтр для получения ПОСЛЕДНИХ присоединившихся
from telethon.tl.types import ChannelParticipantsRecent, ChannelParticipantsSearch
from telethon.errors.rpcerrorlist import UserNotParticipantError, FloodWaitError

# --- ПРИНУДИТЕЛЬНАЯ КОДИРОВКА UTF-8 ДЛЯ ВЫВОДА ---
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8')

# --- КОНФИГУРАЦИЯ ---
load_dotenv()
API_ID = os.getenv('api_id')
API_HASH = os.getenv('api_hash')
PHONE_NUMBER = os.getenv('phone_number')
CHANNEL_USERNAME = 'homeinv'
current_date_str = datetime.now().strftime("%Y_%m_%d") # Получаем текущую дату и форматируем ее в строку 'ГГГГ_мм_дд'
WHITELIST_CSV = f"{current_date_str}_subscribers_whitelist.csv" # Собираем полное имя файла с помощью f-строки
KICKED_LOG_FILENAME = 'kicked_users.csv' # <--- ФАЙЛ ДЛЯ ЛОГОВ
KICK_DELAY_SECONDS = 3  # <--- Увеличил задержку для безопасности

# Создаем клиент
client = TelegramClient('homeinv_session', API_ID, API_HASH)


async def kick_by_date(channel_entity, date_str: str, force_delete=False):
    """
    Основная функция для удаления подписчиков, присоединившихся ПОСЛЕ указанной даты.
    Корректно обрабатывает время, указанное в Московском часовом поясе.
    """
    try:
        # Устанавливаем часовой пояс Москвы
        try:
            moscow_tz = ZoneInfo("Europe/Moscow")
        except ZoneInfoNotFoundError:
            print("[!] Ошибка: Часовой пояс 'Europe/Moscow' не найден.")
            print("[!] Для Windows может потребоваться установка пакета tzdata: pip install tzdata")
            return

        # Парсим введенную строку как "наивное" время (без часового пояса)
        naive_date = datetime.strptime(date_str, '%Y-%m-%d %H:%M:%S')

        # Присваиваем этому времени часовой пояс Москвы (локализуем)
        localized_date = naive_date.replace(tzinfo=moscow_tz)

        # Конвертируем московское время в UTC для сравнения с данными от Telegram
        kick_after_date_utc = localized_date.astimezone(timezone.utc)
        
        print(f"[*] Целевая дата: все, кто подписался ПОСЛЕ {date_str} (Moscow Time), будут удалены.")
        print(f"    (Это соответствует {kick_after_date_utc.strftime('%Y-%m-%d %H:%M:%S UTC')})")

    except ValueError:
        print(f"[!] Ошибка: Неверный формат даты. Используйте 'YYYY-MM-DD HH:MM:SS'.")
        return

    log_header = ['user_id', 'username', 'first_name', 'last_name', 'join_date', 'is_bot', 'access_hash']
    if not os.path.exists(KICKED_LOG_FILENAME):
        with open(KICKED_LOG_FILENAME, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=log_header, delimiter=';')
            writer.writeheader()
    
    total_kicked_count = 0
    offset = 0
    limit = 200

    while True:
        print(f"\n[*] Запрашиваю порцию самых новых подписчиков (offset: {offset})...")
        try:
            result = await client(GetParticipantsRequest(
                channel=channel_entity,
                filter=ChannelParticipantsRecent(),
                offset=offset,
                limit=limit,
                hash=0
            ))
        except FloodWaitError as e:
            print(f"[!] FloodWaitError. Жду {e.seconds} сек...")
            await asyncio.sleep(e.seconds + 5)
            continue
        except Exception as e:
            print(f"[!] Критическая ошибка при получении участников: {e}")
            break

        if not result.users:
            print("[+] Больше нет участников для проверки. Завершаю работу.")
            break

        users_map = {user.id: user for user in result.users}
        participants_to_kick = []
        stop_processing = False

        for p in result.participants:
            user = users_map.get(p.user_id)
            if not user or not hasattr(p, 'date'):
                continue

            # Удаляем тех, кто присоединился ПОСЛЕ указанной даты (их дата больше целевой)
            if p.date > kick_after_date_utc:
                participants_to_kick.append((user, p.date))
                print(f"[DEBUG] Добавлен для удаления: {user.id} (дата: {p.date.strftime('%Y-%m-%d %H:%M:%S UTC')})")
            else:
                # Если встретили пользователя, который присоединился ДО или В целевую дату
                # print(f"[*] Найден участник ({user.id}), присоединившийся до/в целевую дату ({p.date.strftime('%Y-%m-%d %H:%M:%S UTC')}).")
                # print(f"[*] Сравнение: {p.date.strftime('%Y-%m-%d %H:%M:%S UTC')} <= {kick_after_date_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")
                
                # Если мы уже собрали пользователей для удаления, продолжаем до тех пор,
                # пока не найдем достаточно старых пользователей подряд
                if len(participants_to_kick) == 0:
                    stop_processing = True
                    break

        if not participants_to_kick:
            if stop_processing:
                print("[+] В этой порции нет подписчиков для удаления (все старше целевой даты). Чистка завершена.")
                break
            else:
                # Переходим к следующей порции
                offset += limit
                continue
        
        print("-" * 50)
        print(f"[!] Найдено {len(participants_to_kick)} подписчиков для удаления:")
        print(f"{'ID':<12} {'Username':<20} {'Full Name':<25} {'Join Date (UTC)'}")
        print("-" * 80)
        
        for user, join_date in participants_to_kick:
            username = f"@{user.username}" if user.username else "N/A"
            full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
            print(f"{user.id:<12} {username:<20} {full_name:<25} {join_date.strftime('%Y-%m-%d %H:%M:%S')}")
        print("-" * 50)

        if not force_delete:
            try:
                confirm = await asyncio.to_thread(
                    input, f"[?] Вы уверены, что хотите удалить этих {len(participants_to_kick)} пользователей? (введите 'yes' для подтверждения): "
                )
                if confirm.lower() != 'yes':
                    print("[*] Операция отменена пользователем. Завершение работы.")
                    break
            except (KeyboardInterrupt, asyncio.CancelledError):
                 print("\n[*] Операция прервана.")
                 break

        kicked_in_batch = 0
        
        for i, (user, join_date) in enumerate(participants_to_kick):
            try:
                print(f"[*] Удаляю [{i+1}/{len(participants_to_kick)}]: ID={user.id}, Username={user.username or 'N/A'}")
                await client.kick_participant(channel_entity, user)
                
                with open(KICKED_LOG_FILENAME, 'a', encoding='utf-8', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=log_header, delimiter=';')
                    writer.writerow({
                        'user_id': user.id, 
                        'username': user.username or '', 
                        'first_name': user.first_name or '',
                        'last_name': user.last_name or '', 
                        'join_date': join_date.strftime('%Y-%m-%d %H:%M:%S'), 
                        'is_bot': user.bot,
                        'access_hash': user.access_hash
                    })

                kicked_in_batch += 1
                print(f"    -> Успешно. Пауза {KICK_DELAY_SECONDS} сек.")
                await asyncio.sleep(KICK_DELAY_SECONDS)

            except UserNotParticipantError:
                print("    -> Ошибка: Пользователь уже не является участником.")
            except FloodWaitError as e:
                print(f"[!] Получен FloodWaitError. Жду {e.seconds} секунд...")
                await asyncio.sleep(e.seconds + 5)
                print("[!] Прерываю текущую пачку из-за FloodWait. Запустите скрипт снова через некоторое время.")
                stop_processing = True
                break
            except Exception as e:
                print(f"[!] Не удалось удалить пользователя {user.id}. Ошибка: {e}")
        
        total_kicked_count += kicked_in_batch
        print(f"[+] В этой порции удалено {kicked_in_batch} подписчиков.")

        if stop_processing:
            print("[+] Чистка завершена из-за FloodWait или других ограничений.")
            break
        
        # Переходим к следующей порции
        offset += limit
        print("[*] Пауза перед запросом следующей порции...")
        await asyncio.sleep(5)

    print(f"\n[+] Всего удалено подписчиков: {total_kicked_count}.")
    print(f"[*] Лог удаленных пользователей сохранен в файл: {KICKED_LOG_FILENAME}")

async def main():
    parser = argparse.ArgumentParser(description="Скрипт для управления подписчиками Telegram канала.")
    subparsers = parser.add_subparsers(dest='command', required=True, help='Доступные команды')
    
    subparsers.add_parser('save', help='Сохранить доступных подписчиков в CSV (для каналов - неполный список).')
    subparsers.add_parser('info', help='Показать диагностическую информацию о канале.')

    parser_kick = subparsers.add_parser('kickbydate', help='Удалить подписчиков, присоединившихся ПОСЛЕ указанной даты.')
    # ИЗМЕНЕН ПАРАМЕТР И ПОДСКАЗКА
    parser_kick.add_argument('--after-date', type=str, required=True, help="Целевая дата в формате 'YYYY-MM-DD HH:MM:SS'. Все, кто новее, будут удалены.")
    parser_kick.add_argument('--yes', '-y', action='store_true', help='Пропустить подтверждение и немедленно начать удаление.')
    
    args = parser.parse_args()

    await client.start(phone=PHONE_NUMBER)
    print("[+] Клиент успешно запущен.")
    
    try:
        channel = await client.get_entity(CHANNEL_USERNAME)
        print(f"[*] Работаю с каналом: {channel.title} (ID: {channel.id})")

        if args.command == 'save':
            await save_subscribers_to_csv(channel)
        elif args.command == 'info':
            await get_channel_info(channel)
        elif args.command == 'kickbydate':
            await kick_by_date(channel, args.after_date, args.yes)

    except ValueError:
        print(f"[!] Канал '{CHANNEL_USERNAME}' не найден. Проверьте правильность имени пользователя.")
    except Exception as e:
        import traceback
        print(f"[!] Произошла критическая ошибка: {e}")
        traceback.print_exc() # Печатаем полный traceback для отладки
    finally:
        await client.disconnect()
        print("[+] Клиент отключен.")


async def save_subscribers_to_csv(channel_entity):
    print(f"[*] Начинаю сохранение подписчиков канала '{CHANNEL_USERNAME}' в файл {WHITELIST_CSV}...")
    print("[!] ВНИМАНИЕ: для каналов этот метод сохранит только последних подписчиков из-за ограничений Telegram API.")

    csv_header = ['user_id', 'username', 'first_name', 'last_name', 'join_date', 'is_bot', 'access_hash']
    all_participants_data = []

    try:
        offset = 0
        limit = 200
        
        while True:
            print(f"[*] Запрашиваю порцию подписчиков (offset: {offset})...")
            
            try:
                # Используем GetParticipantsRequest для получения информации о дате присоединения
                result = await client(GetParticipantsRequest(
                    channel=channel_entity,
                    filter=ChannelParticipantsRecent(),
                    offset=offset,
                    limit=limit,
                    hash=0
                ))
            except FloodWaitError as e:
                print(f"[!] FloodWaitError. Жду {e.seconds} сек...")
                await asyncio.sleep(e.seconds + 5)
                continue
            except Exception as e:
                print(f"[!] Ошибка при получении участников: {e}")
                break

            if not result.users:
                print("[+] Больше нет участников для сохранения.")
                break

            # Создаем мапу пользователей
            users_map = {user.id: user for user in result.users}
            
            # Обрабатываем участников с информацией о дате присоединения
            for participant in result.participants:
                user = users_map.get(participant.user_id)
                if not user:
                    continue
                
                # Получаем дату присоединения из объекта participant
                join_date_str = ""
                if hasattr(participant, 'date') and participant.date:
                    join_date_str = participant.date.strftime('%Y-%m-%d %H:%M:%S')
                
                all_participants_data.append({
                    'user_id': user.id,
                    'username': user.username or '',
                    'first_name': user.first_name or '',
                    'last_name': user.last_name or '',
                    'join_date': join_date_str,
                    'is_bot': user.bot,
                    'access_hash': user.access_hash
                })
            
            print(f"[*] Собрано {len(all_participants_data)} участников...")
            
            # Если получили меньше участников, чем лимит, значит это последняя порция
            if len(result.users) < limit:
                print("[+] Достигнут конец списка участников.")
                break
                
            offset += limit
            
            # Небольшая пауза между запросами
            await asyncio.sleep(1)

    except Exception as e:
        print(f"[!] Критическая ошибка при получении участников: {e}")

    print(f"\n[+] Всего собрано {len(all_participants_data)} подписчиков.")

    if not all_participants_data:
        print("[!] Не удалось получить данные участников. Проверьте права доступа к каналу.")
        return

    try:
        with open(WHITELIST_CSV, 'w', encoding='utf-8', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=csv_header, delimiter=';')
            writer.writeheader()
            writer.writerows(all_participants_data)
        print(f"[+] Данные успешно сохранены в {WHITELIST_CSV}")
        
        # Показываем примеры первых нескольких записей для проверки
        print(f"\n[*] Примеры сохраненных данных (первые 3 записи):")
        for i, participant in enumerate(all_participants_data[:3]):
            username = f"@{participant['username']}" if participant['username'] else "N/A"
            full_name = f"{participant['first_name']} {participant['last_name']}".strip()
            print(f"  {participant['user_id']} | {username} | {full_name} | {participant['join_date']}")
            
    except Exception as e:
        print(f"[!] Ошибка при записи в CSV: {e}")


async def get_channel_info(channel_entity):
    print(f"[*] Диагностика канала '{CHANNEL_USERNAME}'...")
    try:
        full_channel = await client.get_entity(channel_entity)
        print(f"[+] Название канала: {full_channel.title}")
        print(f"[+] ID канала: {full_channel.id}")
        print(f"[+] Тип: {'Канал' if full_channel.broadcast else 'Группа'}")
        print(f"[+] Участников (по API): {getattr(full_channel, 'participants_count', 'Неизвестно')}")
        from telethon.tl.functions.channels import GetFullChannelRequest
        full_info = await client(GetFullChannelRequest(channel_entity))
        print(f"[+] Могу ли я видеть участников: {full_info.full_chat.can_view_participants}")
        print("\n[*] Тестирование методов получения участников (limit=200):")
        
        for filter_name, filter_obj in [("Recent", ChannelParticipantsRecent()), ("Search ''", ChannelParticipantsSearch(''))]:
            try:
                result = await client(GetParticipantsRequest(channel=channel_entity, filter=filter_obj, offset=0, limit=200, hash=0))
                print(f"[+] GetParticipantsRequest с фильтром {filter_name}: получено {len(result.participants)} участников.")
            except Exception as e:
                print(f"[!] GetParticipantsRequest с фильтром {filter_name} не сработал: {e}")
    except Exception as e:
        print(f"[!] Ошибка при диагностике: {e}")

if __name__ == "__main__":
    asyncio.run(main())