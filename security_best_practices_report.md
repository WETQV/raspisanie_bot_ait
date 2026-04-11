# Security Audit Report

Дата: 2026-04-11  
Проект: `raspisanie_bot_ait`

## Executive Summary

Критических уязвимостей в коде не найдено. В проекте уже есть важные защитные меры: `.env` игнорируется Git, SQL-запросы параметризованы, HTML в расписании в основном экранируется, имена скачиваемых файлов нормализуются через `Path(filename).name`, path traversal покрыт тестом.

Основные риски лежат вокруг доверия к внешнему сайту и PDF: бот берет ссылки из HTML колледжа, скачивает и парсит файл в основном процессе, при этом не ограничивает домен скачивания, размер файла и ресурсные лимиты парсера. Отдельно небезопасна документация деплоя: systemd unit запускает бота от `root`.

Статические проверки:

- `bandit -r . -f json`: 1 Low finding, `B311 random.uniform`, фактически не security issue в этом контексте.
- `semgrep --config=p/python`: 0 findings.

## High

### SEC-001: systemd unit запускает бота от root

Severity: High  
CWE: CWE-250 Execution with Unnecessary Privileges  
OWASP: Security Misconfiguration

Место:

- `README.md:228`
- `docs/DEPLOY_DEBIAN11.md:87`

Код/конфигурация:

```ini
User=root
WorkingDirectory=/root/raspisanie_bot_ait
ExecStart=/root/raspisanie_bot_ait/venv/bin/python /root/raspisanie_bot_ait/main.py
```

Почему опасно:

Бот регулярно скачивает и парсит внешний PDF через `pdfplumber`. Если в парсере PDF, зависимостях или окружении появится эксплуатация RCE, код будет выполняться с правами root. Даже без RCE баги в приложении или ошибочные скрипты получают максимальные права на VPS.

Минимальный безопасный фикс:

Создать отдельного пользователя, например `aitbot`, перенести проект в `/opt/raspisanie_bot_ait` или `/srv/raspisanie_bot_ait`, выдать владельца только этому пользователю, а в unit указать:

```ini
User=aitbot
Group=aitbot
WorkingDirectory=/opt/raspisanie_bot_ait
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=/opt/raspisanie_bot_ait/downloads /opt/raspisanie_bot_ait/bot_database.db
```

PoC:

Если любой код внутри процесса выполнит `Path("/root/pwned").write_text("x")`, при текущем unit это сработает. После перевода на отдельного пользователя запись в root-owned paths должна падать с `PermissionError`.

## Medium

### SEC-002: SSRF / arbitrary outbound request через ссылки из HTML

Severity: Medium  
CWE: CWE-918 Server-Side Request Forgery  
OWASP: Server-Side Request Forgery

Место:

- `scraper/link_finder.py:66-69`
- `scraper/schedule_scraper.py:139`
- `services/schedule_updater.py:176`

Код:

```python
def _normalize_url(self, url: str) -> str | None:
    if not url:
        return None
    return urljoin(self.base_url, url)
```

```python
async with session.get(url, timeout=timeout) as response:
```

Почему опасно:

`urljoin()` сохраняет абсолютные URL. Если HTML страницы расписания будет скомпрометирован или в него попадет внешняя ссылка, бот скачает `http://127.0.0.1:...`, `http://169.254.169.254/...` или внутренний адрес VPS. Даже если файл потом не пройдет PDF-валидацию, сам HTTP-запрос уже выполнен из внутренней сети сервера.

Минимальный безопасный фикс:

После `urljoin()` разобрать URL через `urllib.parse.urlparse` и разрешать только `https` и ожидаемый host (`aitanapa.ru`, при необходимости `www.aitanapa.ru`). Редиректы тоже нужно контролировать: либо `allow_redirects=False`, либо после каждого редиректа проверять финальный URL по тем же правилам. Для надежности добавить запрет private/link-local IP, если когда-либо появятся разрешенные внешние домены.

PoC:

В HTML страницы достаточно подставить:

```html
<a href="http://127.0.0.1:8080/admin.pdf">Расписание занятий</a>
```

`LinkFinder._normalize_url()` вернет этот абсолютный URL, а `ScheduleScraper.download_file()` выполнит запрос с VPS.

### SEC-003: нет лимита размера скачиваемого файла

Severity: Medium  
CWE: CWE-400 Uncontrolled Resource Consumption  
OWASP: Security Logging and Monitoring / DoS class risk

Место:

- `scraper/schedule_scraper.py:26`
- `scraper/schedule_scraper.py:166-169`

Код:

```python
MIN_PDF_SIZE = 5 * 1024
...
async for chunk in response.content.iter_chunked(8192):
    if chunk:
        file_obj.write(chunk)
        total += len(chunk)
```

Почему опасно:

Есть минимальный размер PDF, но нет максимального размера. Внешний сервер может отдать очень большой файл или долго стримить данные. Это может забить диск в `downloads/`, подвесить обновления и привести к отказу в обслуживании бота.

Минимальный безопасный фикс:

Добавить `MAX_PDF_SIZE`, например 20-50 MB. Проверять `Content-Length` до скачивания, а во время стриминга прерывать запись, если `total > MAX_PDF_SIZE`. После превышения лимита временный файл должен удаляться через текущий `AtomicFileReplace`.

PoC:

Если ссылка расписания указывает на endpoint, который отдает `b"%PDF" + b"A" * 2_000_000_000`, текущий код будет писать данные до таймаута или заполнения диска.

### SEC-004: парсинг внешнего PDF выполняется без отдельного sandbox/timeout

Severity: Medium  
CWE: CWE-400 Uncontrolled Resource Consumption  
OWASP: Security Misconfiguration / DoS class risk

Место:

- `scraper/schedule_scraper.py:50`
- `parser/schedule_parser.py:340`
- `services/schedule_updater.py:108`

Код:

```python
with pdfplumber.open(filepath) as pdf:
```

```python
parser = ScheduleParser(file_path)
data = parser.parse(self.config.group_name)
```

Почему опасно:

PDF является внешним недоверенным вводом. Даже валидный PDF может быть очень тяжелым для `pdfplumber`/`pdfminer`: много страниц, огромные content streams, сложные объекты. Сейчас парсинг идет в основном процессе бота, поэтому CPU/RAM spike блокирует обработку Telegram и планировщик.

Минимальный безопасный фикс:

Сначала закрыть SEC-003. Затем добавить лимит страниц и времени парсинга. Практичный вариант: запускать парсинг в отдельном subprocess с timeout, ограничением памяти на Linux через systemd (`MemoryMax`, `CPUQuota`) или `resource.setrlimit`, и возвращать только JSON-результат. Минимальный кодовый фикс без subprocess: проверять `len(pdf.pages)` в `_validate_pdf_sync` и отбрасывать PDF с числом страниц выше ожидаемого лимита.

PoC:

PDF с тысячами страниц или сильно раздутыми streams пройдет проверку `%PDF` и может заставить `pdfplumber.open()` или обход `for page in pdf.pages` потреблять CPU/RAM до зависания процесса.

## Low

### SEC-005: HTML injection в admin notification через текст исключения

Severity: Low  
CWE: CWE-79 Improper Neutralization of Input During Web Page Generation, Telegram HTML variant  
OWASP: Injection

Место:

- `services/schedule_updater.py:112-114`
- `services/schedule_service.py:170`

Код:

```python
await self.service.notify_admins(
    f"⚠️ Ошибка парсинга: {exc}",
    "parse_failed",
    THROTTLE_LONG,
)
```

Почему опасно:

Бот создан с `parse_mode=ParseMode.HTML`, а `notify_admins()` отправляет текст как есть. Если исключение содержит HTML-подобный текст из внешнего файла или URL, Telegram попытается интерпретировать его как разметку. Это не браузерный XSS, но возможно искажение админского сообщения, скрытие части текста или отправка кликабельной ссылки.

Минимальный безопасный фикс:

Перед вставкой исключения использовать `html.escape(str(exc), quote=False)` или сделать `notify_admins()` безопасным по умолчанию: экранировать динамические части, а не весь текст с намеренной разметкой. Для этого удобно принимать уже готовый safe HTML только из доверенных мест.

PoC:

Если исключение будет иметь текст `bad <a href="tg://user?id=1">click</a>`, админ получит не буквальный текст ошибки, а HTML-разметку Telegram.

### SEC-006: зависимости не закреплены точными версиями

Severity: Low  
CWE: CWE-1104 Use of Unmaintained Third Party Components / supply chain risk  
OWASP: Vulnerable and Outdated Components

Место:

- `requirements.txt:1-9`

Код:

```text
aiogram>=3.17.0
aiohttp>=3.11.11
pdfplumber>=0.11.5
...
```

Почему опасно:

Нижние границы (`>=`) делают деплой нерепродуцируемым: при `pip install -r requirements.txt --upgrade` можно получить новые major/minor версии и новые transitive dependencies. Это повышает риск внезапной несовместимости или supply-chain инцидента.

Минимальный безопасный фикс:

Ввести lock-файл: `pip-tools` (`requirements.in` + сгенерированный `requirements.txt` с `==` и hashes) или `uv.lock`. Для VPS-деплоя ставить зависимости из lock-файла без `--upgrade`, обновлять их отдельным PR.

PoC:

Сегодня `aiohttp>=3.11.11` может поставить одну версию, через месяц другую. Если новая версия или ее транзитивная зависимость ломает TLS/redirect behavior или содержит уязвимость, production получит ее без ревью кода.

### SEC-007: tracked probe script привязан к production paths

Severity: Low  
CWE: CWE-489 Active Debug Code

Место:

- `tmp_probe_odl_default/run_weekly_probe.py:7`
- `tmp_probe_odl_default/run_weekly_probe.py:20`

Код:

```python
load_dotenv(Path('/root/raspisanie_bot_ait/.env'))
...
conn = sqlite3.connect('/root/raspisanie_bot_ait/bot_database.db')
```

Почему опасно:

Скрипт из `tmp_probe_*` закоммичен и напрямую указывает на production `.env` и production SQLite. Его случайный запуск на сервере может использовать реальный `BOT_TOKEN` и вызвать `weekly_preview_mailing()`, то есть отправить сообщения пользователям или изменить metadata.

Минимальный безопасный фикс:

Удалить временный probe из Git или перенести в `tools/` с явным `--env-path`, `--db-path`, `--dry-run` и защитой от запуска без флага `--confirm-production`. Добавить `tmp_probe_*` в `.gitignore` целиком, а не только JSON внутри.

PoC:

На сервере выполнить:

```bash
python tmp_probe_odl_default/run_weekly_probe.py
```

Скрипт загрузит `/root/raspisanie_bot_ait/.env`, импортирует реальный bot runtime и запустит weekly preview mailing.

## Not Findings

- SQL injection: не обнаружено. Динамические значения передаются через `?` параметры в `database.py`.
- Path traversal в имени скачиваемого файла: закрыто через `Path(filename).name` и проверку `target.parent`, есть тест `test_resolve_download_target_blocks_path_traversal`.
- Telegram HTML injection в расписании: основной вывод расписания экранируется через `escape_html()` / `html.escape()`, есть тест `test_format_schedule_message_escapes_html`.
- Secrets in Git: `.env` существует локально, но `git ls-files` его не показывает; `.gitignore:1` игнорирует `.env`.

## Priority Fix Order

1. Перевести systemd unit на отдельного пользователя и hardening options.
2. Добавить allowlist scheme/host и redirect validation для скачиваемых URL.
3. Добавить `MAX_PDF_SIZE`, лимит страниц и timeout/sandbox для PDF parsing.
4. Экранировать динамические части admin notifications.
5. Убрать production-bound probe script из tracked tree.
6. Ввести lock-файл зависимостей.
