# Структура проекта

## Обзор

Бот расписания для Telegram с модульной архитектурой для АИТ в Анапе.

## Структура файлов

```
raspisanie_bot_ait/
│
├── main.py                    # 🚀 Точка входа
├── bot.py                     # Хендлеры команд и основная логика
├── config.py                  # Конфигурация из переменных окружения
├── database.py                # Класс Database с единым соединением
├── requirements.txt           # Зависимости Python
│
├── .env                       # Переменные окружения (сделайте основываясь на example)
├── .env.example               # Шаблон переменных окружения
├── .gitignore                 # Игнорируемые файлы
│
├── models/                    # 📦 Модели данных
│   ├── __init__.py
│   └── lesson.py             # Lesson, DaySchedule (dataclass)
│
├── services/                  # 🔧 Бизнес-логика
│   ├── __init__.py
│   ├── schedule_service.py   # Рассылка сообщений и уведомления админов
│   └── schedule_updater.py   # Логика проверки и обновления расписания
│
├── middleware/                # 🛡️ Middleware для aiogram
│   ├── __init__.py
│   └── access_middleware.py  # Проверка доступа (зарегистрированные чаты/админы)
│
├── parser/                    # 📄 Парсер PDF расписания
│   ├── __init__.py
│   ├── lesson_extractor.py   # Извлечение кабинета и предмета из строки
│   └── schedule_parser.py    # Парсинг PDF файла в структурированные данные
│
├── scraper/                   # 🌐 Скрапер сайта (асинхронный)
│   ├── __init__.py
│   ├── schedule_scraper.py   # Основной скрапер (aiohttp)
│   ├── link_finder.py        # Поиск ссылок на файлы в HTML
│   └── atomic_file.py        # Атомарная замена файлов с бэкапом
│
└── downloads/                 # 📥 Скачанные PDF файлы (создаётся автоматически)
```

## Описание модулей

### main.py
**Точка входа приложения.** Загружает переменные окружения и запускает `bot.main()`.

### bot.py
Содержит:
- Хендлеры команд (`/start`, `/today`, `/tomorrow`, `/update`)
- Функции форматирования сообщений
- Фоновые задачи (проверка расписания, вечерняя рассылка)
- Lifecycle hooks (startup, shutdown)

### config.py
Класс `Config` для загрузки настроек из переменных окружения:
- `BOT_TOKEN` — токен бота от BotFather
- `GROUP_NAME` — название группы (по умолчанию "ИСП-3-22")
- `ADMIN_IDS` — список ID админов через запятую (для проверки в ЛС)

### database.py
Класс `Database`:
- Единое соединение с БД (не создаёт новое на каждый запрос)
- WAL режим для лучшей производительности
- Индексы для ускорения поиска
- Методы для работы с чатами, расписанием, метаданными

### models/lesson.py
Модели данных:
- `Lesson` — один урок (пара)
- `DaySchedule` — расписание на день

### services/schedule_service.py
`ScheduleService`:
- `broadcast_message()` — рассылка во все чаты с rate limiting
- `notify_admins()` — уведомления админов с throttle
- Обработка ошибок Telegram API (автоудаление заблокированных чатов)

### services/schedule_updater.py
`ScheduleUpdater`:
- `check_and_update()` — основная логика проверки обновлений
- `filter_links()` — фильтрация ссылок на расписание
- `_fetch_links()` — получение ссылок с сайта
- `_parse_and_save()` — парсинг и сохранение расписания

### middleware/access_middleware.py
`AccessMiddleware`:
- Пропускает только зарегистрированные чаты и админов
- Показывает сообщение об ограничении для `/start` от неавторизованных

### parser/lesson_extractor.py
`LessonExtractor`:
- Извлекает кабинет и предмет из сырой строки PDF
- Несколько стратегий поиска кабинета
- Очистка Unicode, удаление преподавателя, автозамены

### parser/schedule_parser.py
`ScheduleParser`:
- Парсит PDF файл расписания
- Извлекает метаданные (период)
- Фильтрует строки по группе
- Обрабатывает строки в структурированное расписание

### scraper/schedule_scraper.py
`ScheduleScraper` (асинхронный):
- `get_schedule_links()` — получение ссылок с сайта (aiohttp)
- `download_file()` — скачивание файла (aiohttp + AtomicFileReplace)
- SHA256 хеширование по чанкам

### scraper/link_finder.py
`LinkFinder`:
- Поиск ссылок на файлы в HTML (несколько стратегий)
- Дедупликация URL
- Извлечение заголовков файлов

### scraper/atomic_file.py
`AtomicFileReplace`:
- Контекстный менеджер для атомарной замены файлов
- Автоматический бэкап и откат при ошибке

## Поток данных

```
1. Скрапер получает ссылки с сайта
   ↓
2. ScheduleUpdater фильтрует ссылки
   ↓
3. Скачивание PDF файла
   ↓
4. ScheduleParser парсит PDF
   ↓
5. LessonExtractor извлекает кабинет и предмет
   ↓
6. Сохранение в Database
   ↓
7. ScheduleService рассылает сообщения
```

## Зависимости

- `aiogram` — Telegram Bot API
- `aiohttp` — асинхронные HTTP запросы
- `aiofiles` — асинхронная работа с файлами
- `aiosqlite` — асинхронная работа с SQLite
- `apscheduler` — планировщик задач
- `pdfplumber` — парсинг PDF
- `beautifulsoup4` — парсинг HTML
- `python-dotenv` — загрузка переменных окружения

## Запуск

**Способ запуска:**

```bash
python main.py
```

## Переменные окружения

Создайте файл `.env` на основе `.env.example`:

```env
BOT_TOKEN=твой_токен_от_BotFather
GROUP_NAME=ИСП-3-22
ADMIN_IDS=735412766
```

## База данных

Файл `bot_database.db` создаётся автоматически при первом запуске.

Таблицы:
- `chats` — зарегистрированные чаты
- `schedule` — расписание занятий
- `metadata` — метаданные (хеши, даты рассылок)

## Логирование

Логи выводятся в stdout/stderr. При запуске через systemd доступны через `journalctl -u ait-bot`.


# Пример деплоя на Debian 11 (Python 3.12)

Инструкция по развёртыванию бота расписания на VPS с Debian 11.

## 1) Установка Python 3.12

```bash
apt update
apt install -y \
  build-essential \
  wget \
  libssl-dev \
  zlib1g-dev \
  libbz2-dev \
  libreadline-dev \
  libsqlite3-dev \
  libffi-dev \
  liblzma-dev \
  tk-dev \
  uuid-dev \
  xz-utils \
  libncursesw5-dev \
  libgdbm-dev \
  libdb5.3-dev \
  libexpat1-dev

cd /usr/src
wget https://www.python.org/ftp/python/3.12.5/Python-3.12.5.tgz
tar xzf Python-3.12.5.tgz
cd Python-3.12.5

./configure --enable-optimizations
make -j"$(nproc)"
make altinstall
```

Проверка:
```bash
python3.12 --version
```

## 2) Подготовка проекта

### 2.1 Клонирование/загрузка проекта

```bash
# Если используете git:
git clone https://github.com/WETQV/raspisanie_ait_bot.git /root/raspisanie_bot_ait
cd /root/raspisanie_bot_ait

# Или загрузите файлы через scp/sftp в /root/raspisanie_bot_ait
```

### 2.2 Создание виртуального окружения

```bash
cd /root/raspisanie_bot_ait
python3.12 -m venv venv
source venv/bin/activate
python --version  # Должно быть 3.12.x
```

### 2.3 Установка зависимостей

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 2.4 Настройка переменных окружения

```bash
# Скопируйте шаблон
cp .env.example .env

# Отредактируйте .env и укажите реальный токен бота
nano .env
```

Содержимое `.env`:
```env
BOT_TOKEN=ваш_токен_от_BotFather
GROUP_NAME=ИСП-3-22
ADMIN_IDS=735412766
```

**ВАЖНО:** Токен должен быть получен от @BotFather.

## 3) Первый запуск (проверка)

```bash
cd /root/raspisanie_bot_ait
source venv/bin/activate
python main.py
```

Остановить: `Ctrl+C`

Если всё работает — переходим к настройке systemd.

## 4) Настройка systemd (автозапуск)

Создайте файл `/etc/systemd/system/ait-bot.service`:

```bash
nano /etc/systemd/system/ait-bot.service
```

Содержимое:

```ini
[Unit]
Description=AIT Schedule Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/raspisanie_bot_ait
ExecStart=/root/raspisanie_bot_ait/venv/bin/python /root/raspisanie_bot_ait/main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

Либо создайте файл на своём устройстве и перекиньте на сервер.

Применить и запустить:

```bash
systemctl daemon-reload
systemctl enable ait-bot
systemctl start ait-bot
```

### Управление сервисом

```bash
# Проверить статус
systemctl status ait-bot

# Остановить
systemctl stop ait-bot

# Перезапустить
systemctl restart ait-bot

# Посмотреть логи (в реальном времени)
journalctl -u ait-bot -f

# Посмотреть последние 100 строк логов
journalctl -u ait-bot -n 100
```

### Доступ и регистрация групп

- Команды бота обрабатываются только в зарегистрированных чатах и у пользователей из ADMIN_IDS (в т.ч. в личке).

- Зарегистрировать новую группу может только админ: добавьте бота в группу и отправь /start от своего аккаунта (твой ID должен быть в ADMIN_IDS). После этого бот работает для всех участников этой группы.

- Если /start напишет не админ в ещё не зарегистрированном чате, бот ответит: «Регистрация новых групп ограничена» и не добавит чат.

Вот и всё. Теперь у вас будет личный бот для расписания. Остаётся добавить в группу, и написать `/start`.