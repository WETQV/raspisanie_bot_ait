# Telegram-бот расписания АИТ

Бот автоматически забирает PDF-расписание с сайта колледжа, парсит его, сохраняет в SQLite и рассылает обновления в зарегистрированные чаты Telegram.

## Что умеет

- Проверяет сайт по расписанию и находит новый PDF с расписанием.
- Безопасно скачивает PDF в `downloads/` с проверкой расширения, хеша и валидности файла.
- Парсит расписание для одной группы из PDF через координатный парсер, устойчивый к кривым таблицам.
- Хранит несколько недель и выбирает нужную по целевой дате.
- Отправляет расписание на сегодня, на следующий учебный день и полное недельное обновление.
- Рассылает вечернее сообщение на следующий учебный день в `19:00`.
- Разрешает ручное обновление `/update` и принудительный перепарс `/reparse` только администраторам.

## Как это выглядит

Бот рассчитан на простой сценарий: пользователь пишет команду в Telegram и получает уже очищенное расписание без PDF-мусора, склеенных ячеек и обрывков слов.

### `/today` в личных сообщениях

![today](docs/SCREENSHOTS/01_today_dm.png)

### Недельное обновление после парсинга нового PDF

[Полный размер](docs/SCREENSHOTS/02_week_update.png)

![week update preview](docs/SCREENSHOTS/02_week_update_preview.png)

### `/tomorrow` в личных сообщениях

![tomorrow](docs/SCREENSHOTS/04_tomorrow_dm.png)

## Структура проекта

```text
raspisanie_bot_ait/
├── main.py
├── bot.py
├── config.py
├── database.py
├── requirements.txt
├── .env.example
├── middleware/
│   └── access_middleware.py
├── models/
│   └── lesson.py
├── parser/
│   ├── lesson_extractor.py
│   ├── subject_alias_catalog.py
│   └── schedule_parser.py
├── scraper/
│   ├── atomic_file.py
│   ├── link_finder.py
│   └── schedule_scraper.py
├── services/
│   ├── schedule_service.py
│   └── schedule_updater.py
├── tests/
│   └── test_project_hardening.py
└── downloads/
```

## Ключевые модули

### `main.py`

Точка входа. Загружает `.env` и запускает `bot.main()`.

### `bot.py`

Основная логика бота:

- команды `/start`, `/today`, `/tomorrow`, `/update`, `/reparse`;
- форматирование сообщений;
- планировщик фоновых задач;
- восстановление состояния при старте;
- выбор следующего учебного дня с пропуском воскресенья;
- сброс накопившихся pending updates при старте polling.

### `config.py`

Чтение настроек из переменных окружения:

- `BOT_TOKEN`
- `GROUP_NAME`
- `ADMIN_IDS`
- `TELEGRAM_PROXY_URL`
- `TELEGRAM_API_BASE_URL`

### `database.py`

Слой доступа к SQLite:

- одно общее соединение;
- идемпотентный `connect()`;
- таблицы `chats`, `schedule`, `metadata`;
- выбор нужного `week_period` по дате, а не по последней вставленной записи.

### `services/schedule_updater.py`

Оркестрация обновления расписания:

- получение списка ссылок;
- фильтрация лишних файлов;
- скачивание PDF;
- парсинг и сохранение;
- запуск рассылки при новом расписании.

### `services/schedule_service.py`

Отправка сообщений:

- рассылка по всем зарегистрированным чатам;
- удаление чатов, где бот заблокирован;
- уведомления администраторам с throttle;
- отправка PDF и текста отдельными сообщениями, без длинного `caption`.

### `scraper/schedule_scraper.py`

Асинхронный скрапер:

- получает HTML страницы расписания;
- извлекает ссылки на файлы;
- безопасно сохраняет только `pdf`;
- проверяет allowlist хостов, ручные редиректы, размер и число страниц PDF;
- защищается от path traversal через имя файла.

### `parser/schedule_parser.py`

Координатный PDF-парсер:

- извлекает период недели;
- находит блок нужной группы по левому столбцу;
- собирает ячейки по координатам и `chars`, а не через `extract_tables()`;
- мержит данные группы по нескольким страницам.

### `parser/subject_alias_catalog.py`

Каталог устойчивых сокращений предметов:

- нормализует частые сокращения колледжа;
- чинит типовые битые формы из PDF;
- упрощает поддержку без переписывания логики парсера.

### `parser/lesson_extractor.py`

Нормализует содержимое ячеек PDF:

- выделяет кабинет;
- чистит текст;
- убирает хвосты с преподавателями;
- применяет замены предметов.

## Поток данных

```text
1. scraper.get_schedule_links()
2. services.schedule_updater.filter_links()
3. scraper.download_file()
4. parser.schedule_parser.parse()
5. parser.subject_alias_catalog.normalize_subject_alias()
6. database.save_schedule()
7. services.schedule_service.broadcast_message()
```

## Команды бота

- `/start` — регистрирует чат, если команду вызвал администратор.
- `/today` — показывает расписание на сегодня.
- `/tomorrow` — показывает расписание на следующий учебный день.
- `/update` — вручную запускает обновление расписания. Доступно только `ADMIN_IDS`, с простым throttle.
- `/reparse` — принудительно перепарсивает текущий PDF, даже если хеш файла не изменился. Доступно только `ADMIN_IDS`.

## Доступ и регистрация чатов

- Бот отвечает только в зарегистрированных чатах и администраторам.
- Новый чат может зарегистрировать только пользователь, чей Telegram ID указан в `ADMIN_IDS`.
- Если `/start` отправит не админ в незарегистрированном чате, бот ответит, что регистрация ограничена.
- После регистрации чата бот может отвечать всем участникам на обычные команды чтения расписания.
- Ручное обновление `/update` и `/reparse` разрешено только администраторам.

## Переменные окружения

Создайте `.env` на основе [.env.example](.env.example). Реальные значения `.env` не должны попадать в Git.

```env
BOT_TOKEN=ваш_токен_от_BotFather
GROUP_NAME=ИСП-3-22
ADMIN_IDS=Ваш ID телеграм акка (типа 123456789)
TELEGRAM_PROXY_URL=
TELEGRAM_API_BASE_URL=
```

### Примечания

- `BOT_TOKEN` обязателен.
- `ADMIN_IDS` — список числовых Telegram ID через запятую.
- `GROUP_NAME` должен совпадать с названием группы в PDF.
- `TELEGRAM_PROXY_URL` опционален. Используйте его, если VPS может ходить в Telegram только через SOCKS/HTTP-прокси.
- `TELEGRAM_API_BASE_URL` опционален. Используйте его для приватного HTTPS gateway к Telegram Bot API, например через Cloudflare Worker.

## Доступ к Telegram API с VPS

На многих VPS в России прямое соединение с `https://api.telegram.org` не работает. Если бот запускается, но в логах есть `TelegramNetworkError`, `Request timeout error` или polling не стартует, сначала проверьте доступ:

```bash
curl -4 -I https://api.telegram.org
```

Если ответа нет, используйте один из вариантов:

- `TELEGRAM_PROXY_URL=socks5://user:password@host:port`
- `TELEGRAM_PROXY_URL=http://user:password@host:port`
- `TELEGRAM_API_BASE_URL=https://your-private-gateway.example/secret-prefix`

`TELEGRAM_PROXY_URL` подходит для обычного SOCKS/HTTP-прокси. Для SOCKS установлен `aiohttp-socks`.

`TELEGRAM_API_BASE_URL` подходит для приватного gateway, который повторяет формат Telegram Bot API:

```bash
https://your-private-gateway.example/secret-prefix/bot<TOKEN>/<method>
```

Gateway должен проксировать запросы на `https://api.telegram.org`. URL gateway и прокси-credentials храните только в server-side `.env`. Не добавляйте их в README, issue, PR, systemd unit или историю shell-команд, если команда попадёт в логи.

## Установка и безопасный запуск

Для локальной разработки можно запускать `python main.py` вручную. Для production используйте systemd и отдельного Linux-пользователя.

### Локально на Windows

```powershell
python -m venv venv
venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
copy .env.example .env
notepad .env
python main.py
```

### Локально на Linux

```bash
python3.12 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cp .env.example .env
nano .env
python main.py
```

### Production на VPS

Рекомендуемый layout:

- код: `/opt/raspisanie_bot_ait`;
- пользователь: `aitbot`;
- секреты: `/opt/raspisanie_bot_ait/.env`;
- сервис: `ait-bot.service`.

Базовая установка:

```bash
useradd --system --create-home --home-dir /opt/raspisanie_bot_ait --shell /usr/sbin/nologin aitbot
git clone https://github.com/WETQV/raspisanie_bot_ait.git /opt/raspisanie_bot_ait
cd /opt/raspisanie_bot_ait
python3.12 -m venv venv
chown -R aitbot:aitbot /opt/raspisanie_bot_ait
runuser -u aitbot -- venv/bin/pip install --upgrade pip
runuser -u aitbot -- venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env
chown aitbot:aitbot .env
chmod 600 .env
```

Не запускайте `python main.py` вручную на сервере, если systemd-сервис уже работает. Два polling-процесса с одним токеном конфликтуют и могут вести себя непредсказуемо.

## Production через systemd

Пример unit-файла:

```ini
[Unit]
Description=AIT Schedule Bot
After=network.target

[Service]
Type=simple
User=aitbot
Group=aitbot
WorkingDirectory=/opt/raspisanie_bot_ait
ExecStart=/opt/raspisanie_bot_ait/venv/bin/python /opt/raspisanie_bot_ait/main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

Команды управления:

```bash
systemctl daemon-reload
systemctl enable ait-bot
systemctl start ait-bot
systemctl status ait-bot
journalctl -u ait-bot -f
```

Безопасная проверка production без ручного запуска бота:

```bash
systemctl status ait-bot.service --no-pager -l
journalctl -u ait-bot.service -n 120 --no-pager
ps -eo pid,user,cmd | grep -E '[/]opt/raspisanie_bot_ait/.*/python|[/]opt/raspisanie_bot_ait/main.py' | grep -v grep
```

Обновление production:

```bash
cd /opt/raspisanie_bot_ait
runuser -u aitbot -- git fetch origin
runuser -u aitbot -- git reset --hard origin/main
runuser -u aitbot -- venv/bin/pip install -r requirements.txt
runuser -u aitbot -- venv/bin/python -m compileall .
runuser -u aitbot -- venv/bin/python -m unittest discover -s tests -v
systemctl restart ait-bot.service
systemctl status ait-bot.service --no-pager -l
```

Не запускайте на production без явной причины:

- `/update` и `/reparse`;
- ad-hoc Python-скрипты, которые импортируют `bot.py` и вызывают mailing-функции;
- параллельный `python main.py` рядом с systemd.

## База данных

Файл `bot_database.db` создаётся автоматически при первом запуске.

Таблицы:

- `chats` — зарегистрированные чаты и `message_thread_id`;
- `schedule` — строки расписания по неделям и дням;
- `metadata` — служебные значения: хеши, даты рассылок, throttle.

## Зависимости

- `aiogram` — Telegram Bot API
- `aiohttp` — HTTP-запросы
- `aiohttp-socks` — поддержка HTTP/SOCKS-прокси для Telegram API
- `aiosqlite` — SQLite
- `apscheduler` — фоновые задачи
- `pdfplumber` — чтение PDF
- `beautifulsoup4` — разбор HTML
- `python-dotenv` — загрузка `.env`
- `aiofiles` — асинхронная работа с файлами
- `requests` — вспомогательная HTTP-зависимость

`requirements.txt` хранит закрепленные версии для воспроизводимого деплоя. Для планового обновления зависимостей меняйте `requirements.in`, пересобирайте pinned versions отдельным PR и прогоняйте тесты до выкладки.

## Проверки

Статическая компиляция:

```bash
python -m compileall .
```

Тесты:

```bash
python -m unittest discover -s tests -v
```

Сейчас тесты покрывают:

- идемпотентное подключение к БД;
- выбор корректной недели по дате;
- защиту от path traversal в имени файла;
- экранирование HTML в сообщениях;
- логику следующего учебного дня;
- раздельную отправку PDF и текста.

Дополнительные документы:

- [DEPLOY_DEBIAN11.md](docs/DEPLOY_DEBIAN11.md)
- [TEST_SCENARIOS.md](docs/TEST_SCENARIOS.md)

## Ограничения

- Парсер ориентирован на конкретную структуру PDF с сайта колледжа.
- Если структура таблицы в PDF изменится, потребуется доработка `schedule_parser.py`.
- Бот рассчитан на одну основную группу из `GROUP_NAME`.
