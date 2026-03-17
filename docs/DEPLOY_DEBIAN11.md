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
git clone https://github.com/WETQV/raspisanie_ait_bot.git /root/raspisanie_bot_ait
cd /root/raspisanie_bot_ait
python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
nano .env
```

Пример `.env`:

```env
BOT_TOKEN=ваш_токен_от_BotFather
GROUP_NAME=ИСП-3-22
ADMIN_IDS=735412766,123456789
```

Если токен когда-либо был в открытом доступе, его нужно отозвать через `@BotFather` и выпустить новый.

## 3. Проверка ручным запуском

```bash
cd /root/raspisanie_bot_ait
source venv/bin/activate
python main.py
```

Проверь в Telegram:

- `/start`
- `/today`
- `/tomorrow`
- `/update` из админского аккаунта

## 4. systemd

Создай файл `/etc/systemd/system/ait-bot.service`:

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
cd /root/raspisanie_bot_ait
git pull
source venv/bin/activate
pip install -r requirements.txt --upgrade
systemctl start ait-bot
```

## 6. Что важно проверить после деплоя

- Бот стартует через `main.py`, не через `bot.py`.
- `.env` существует и содержит валидный `BOT_TOKEN`.
- `/update` работает только для ID из `ADMIN_IDS`.
- Вечерняя рассылка отправляется на следующий учебный день.
- Файл `bot_database.db` создаётся в корне проекта.

## 7. Резервное копирование базы

```bash
cp /root/raspisanie_bot_ait/bot_database.db /root/backups/bot_database_$(date +%Y%m%d).db
```
