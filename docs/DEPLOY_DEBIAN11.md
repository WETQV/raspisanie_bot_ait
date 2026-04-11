# Деплой на Debian 11

Инструкция для развёртывания бота на VPS с Debian 11 и Python 3.12.

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
adduser --system --group --home /opt/raspisanie_bot_ait aitbot
git clone https://github.com/WETQV/raspisanie_ait_bot.git /opt/raspisanie_bot_ait
chown -R aitbot:aitbot /opt/raspisanie_bot_ait
cd /opt/raspisanie_bot_ait
sudo -u aitbot python3.12 -m venv venv
sudo -u aitbot /opt/raspisanie_bot_ait/venv/bin/pip install --upgrade pip
sudo -u aitbot /opt/raspisanie_bot_ait/venv/bin/pip install -r requirements.txt
sudo -u aitbot cp .env.example .env
chmod 600 .env
nano .env
```

Пример `.env`:

```env
BOT_TOKEN=ваш_токен_от_BotFather
GROUP_NAME=ИСП-3-22
ADMIN_IDS=735412766,123456789
TELEGRAM_PROXY_URL=
```

Если токен когда-либо был в открытом доступе, его нужно отозвать через `@BotFather` и выпустить новый.
Если сервер не достаёт до Telegram API напрямую, заполните `TELEGRAM_PROXY_URL` в `.env`, например `socks5://user:password@host:port`. Реальные данные прокси храните только на сервере.

## 3. Проверка ручным запуском

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

## 4. systemd

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

## 5. Обновление существующего сервиса

Если сервис уже существует:

```bash
systemctl stop ait-bot
cp /opt/raspisanie_bot_ait/bot_database.db /root/backups/bot_database_$(date +%Y%m%d_%H%M%S).db
cd /opt/raspisanie_bot_ait
git pull
chown -R aitbot:aitbot /opt/raspisanie_bot_ait
sudo -u aitbot /opt/raspisanie_bot_ait/venv/bin/pip install -r requirements.txt
systemctl start ait-bot
```

## 6. Что важно проверить после деплоя

- Бот стартует через `main.py`, не через `bot.py`.
- `.env` существует и содержит валидный `BOT_TOKEN`.
- `/update` и `/reparse` работают только для ID из `ADMIN_IDS`.
- Если используется прокси, `TELEGRAM_PROXY_URL` задан только в server-side `.env` и не попал в Git.
- Вечерняя рассылка отправляется на следующий учебный день.
- Файл `bot_database.db` создаётся в корне проекта и принадлежит пользователю `aitbot`.
- После рестарта бот не должен разгребать старую очередь команд из Telegram.

## 7. Резервное копирование базы

```bash
mkdir -p /root/backups
cp /opt/raspisanie_bot_ait/bot_database.db /root/backups/bot_database_$(date +%Y%m%d).db
```
