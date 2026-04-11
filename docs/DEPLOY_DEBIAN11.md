# Деплой на Debian 11/12

Инструкция для развёртывания бота на VPS с Debian и Python 3.12. Production-запуск лучше делать через systemd под отдельным пользователем, а не через ручной `python main.py`.

## 1. Установка Python 3.12

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
python3.12 --version
```

## 2. Подготовка проекта

```bash
useradd --system --create-home --home-dir /opt/raspisanie_bot_ait --shell /usr/sbin/nologin aitbot
git clone https://github.com/WETQV/raspisanie_bot_ait.git /opt/raspisanie_bot_ait
chown -R aitbot:aitbot /opt/raspisanie_bot_ait
cd /opt/raspisanie_bot_ait
runuser -u aitbot -- python3.12 -m venv venv
runuser -u aitbot -- /opt/raspisanie_bot_ait/venv/bin/pip install --upgrade pip
runuser -u aitbot -- /opt/raspisanie_bot_ait/venv/bin/pip install -r requirements.txt
cp .env.example .env
chown aitbot:aitbot .env
chmod 600 .env
nano .env
```

Пример `.env`:

```env
BOT_TOKEN=ваш_токен_от_BotFather
GROUP_NAME=ИСП-3-22
ADMIN_IDS=735412766,123456789
TELEGRAM_PROXY_URL=
TELEGRAM_API_BASE_URL=
```

Если токен когда-либо был в открытом доступе, его нужно отозвать через `@BotFather` и выпустить новый.

## 3. Если Telegram API недоступен с VPS

На части российских VPS прямой доступ к `https://api.telegram.org` может не работать. Проверка:

```bash
curl -4 -I https://api.telegram.org
```

Если команда зависает или возвращает ошибку соединения, укажите в `.env` один из обходных вариантов:

```env
TELEGRAM_PROXY_URL=socks5://user:password@proxy-host:1080
TELEGRAM_PROXY_URL=http://user:password@proxy-host:3128
TELEGRAM_API_BASE_URL=https://your-private-gateway.example/secret-prefix
```

`TELEGRAM_PROXY_URL` использует обычный SOCKS/HTTP-прокси. Для SOCKS в зависимостях есть `aiohttp-socks`.

`TELEGRAM_API_BASE_URL` нужен для приватного HTTPS gateway к Telegram Bot API, например Cloudflare Worker. Gateway должен принимать запросы в формате:

```text
https://your-private-gateway.example/secret-prefix/bot<TOKEN>/<method>
```

и проксировать их на:

```text
https://api.telegram.org/bot<TOKEN>/<method>
```

Реальные значения `TELEGRAM_PROXY_URL`, `TELEGRAM_API_BASE_URL`, пароли и секретные URL-префиксы храните только в server-side `.env`. Не добавляйте их в Git, README, PR, issue, systemd unit или публичные логи.

## 4. Локальная проверка до systemd

```bash
cd /opt/raspisanie_bot_ait
source venv/bin/activate
python main.py
```

На сервере с уже работающим systemd-сервисом не запускайте `python main.py` вручную параллельно сервису: получится второй polling-процесс с тем же токеном. Для проверки production используйте `systemctl status`, `journalctl` и команды в Telegram после перезапуска сервиса.

Проверь в Telegram:

- `/start`
- `/today`
- `/tomorrow`
- `/update` из админского аккаунта
- `/reparse` из админского аккаунта

После проверки остановите ручной процесс через `Ctrl+C` и запускайте production только через systemd.

## 5. systemd

Создай файл `/etc/systemd/system/ait-bot.service`:

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

Применить:

```bash
systemctl daemon-reload
systemctl enable ait-bot
systemctl start ait-bot
systemctl status ait-bot
```

Логи:

```bash
journalctl -u ait-bot -f
```

## 6. Обновление существующего сервиса

Если сервис уже существует:

```bash
mkdir -p /root/backups/raspisanie_bot_ait
systemctl stop ait-bot
cp /opt/raspisanie_bot_ait/.env /root/backups/raspisanie_bot_ait/.env.$(date +%Y%m%d_%H%M%S)
cp /opt/raspisanie_bot_ait/bot_database.db /root/backups/raspisanie_bot_ait/bot_database.$(date +%Y%m%d_%H%M%S).db
cd /opt/raspisanie_bot_ait
runuser -u aitbot -- git fetch origin
runuser -u aitbot -- git reset --hard origin/main
chown -R aitbot:aitbot /opt/raspisanie_bot_ait
runuser -u aitbot -- /opt/raspisanie_bot_ait/venv/bin/pip install -r requirements.txt
runuser -u aitbot -- /opt/raspisanie_bot_ait/venv/bin/python -m compileall .
runuser -u aitbot -- /opt/raspisanie_bot_ait/venv/bin/python -m unittest discover -s tests -v
systemctl start ait-bot
```

## 7. Что важно проверить после деплоя

- Бот стартует через `main.py`, не через `bot.py`.
- `.env` существует и содержит валидный `BOT_TOKEN`.
- `/update` и `/reparse` работают только для ID из `ADMIN_IDS`.
- Если используется прокси, `TELEGRAM_PROXY_URL` задан только в server-side `.env` и не попал в Git.
- Если используется gateway, `TELEGRAM_API_BASE_URL` задан только в server-side `.env` и не попал в Git.
- Вечерняя рассылка отправляется на следующий учебный день.
- Файл `bot_database.db` создаётся в корне проекта и принадлежит пользователю `aitbot`.
- После рестарта бот не должен разгребать старую очередь команд из Telegram.

Безопасные проверки без ручного запуска второго polling-процесса:

```bash
systemctl status ait-bot.service --no-pager -l
journalctl -u ait-bot.service -n 120 --no-pager
ps -eo pid,user,cmd | grep -E '[/]opt/raspisanie_bot_ait/.*/python|[/]opt/raspisanie_bot_ait/main.py' | grep -v grep
```

Не запускайте на production без явной причины:

- параллельный `python main.py` рядом с systemd;
- `/update` и `/reparse`;
- ad-hoc Python-скрипты, которые импортируют `bot.py` и вызывают mailing-функции.

## 8. Резервное копирование базы

```bash
mkdir -p /root/backups/raspisanie_bot_ait
cp /opt/raspisanie_bot_ait/bot_database.db /root/backups/raspisanie_bot_ait/bot_database.$(date +%Y%m%d).db
```
