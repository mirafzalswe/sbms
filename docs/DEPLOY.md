# Деплой SBMS Test Dashboard

Есть 4 варианта — выбери подходящий.

---

## 0. Что подготовлено в проекте

| Файл | Назначение |
|------|-----------|
| `.env.example` | Шаблон переменных окружения |
| `.gitignore` | Исключает `.env`, `__pycache__/`, `test_history/` |
| `requirements.txt` | + `gunicorn`, `waitress` (прод WSGI) |
| `Procfile` | Heroku / Railway |
| `runtime.txt` | Python 3.12.7 для PaaS |
| `render.yaml` | Render.com one-click deploy |
| `Dockerfile` | Контейнеризация |
| `.dockerignore` | Исключения для билда |
| `docker-compose.yml` | Локальный / VPS Docker-запуск |
| `sbms-test.service` | systemd unit для VPS |

В `server.py` убран `debug=True` — режим задаётся `FLASK_DEBUG=1` только для разработки.

---

## 1. Локальная разработка

```bash
cp .env.example .env
# отредактируй .env (SBMS_LOGIN, SBMS_PASSWORD, TEST_MSISDN)

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# dev-режим
FLASK_DEBUG=1 python server.py

# прод-режим локально (через gunicorn)
gunicorn server:app --bind 0.0.0.0:5000 --workers 2 --threads 4
```

Открыть: http://localhost:5000

---

## 2. Docker (проще всего)

### Быстрый запуск

```bash
cp .env.example .env     # заполни секреты
docker compose up -d --build
docker compose logs -f
```

Остановить: `docker compose down`

### Ручной запуск без compose

```bash
docker build -t sbms-test .
docker run -d \
    --name sbms-test \
    --restart unless-stopped \
    -p 5000:5000 \
    --env-file .env \
    -v $(pwd)/test_history:/app/test_history \
    sbms-test
```

---

## 3. VPS (Ubuntu/Debian) + systemd + Nginx

### 3.1 Установка

```bash
# на сервере
sudo apt update
sudo apt install -y python3.12 python3.12-venv nginx git

sudo mkdir -p /opt/sbms-test
sudo chown $USER:$USER /opt/sbms-test
git clone <ваш-репо> /opt/sbms-test
cd /opt/sbms-test

python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

cp .env.example .env
nano .env    # заполнить секреты
```

### 3.2 systemd

```bash
sudo cp sbms-test.service /etc/systemd/system/
sudo chown -R www-data:www-data /opt/sbms-test
sudo systemctl daemon-reload
sudo systemctl enable --now sbms-test
sudo systemctl status sbms-test
journalctl -u sbms-test -f      # логи
```

### 3.3 Nginx reverse proxy (опционально, для HTTPS)

`/etc/nginx/sites-available/sbms-test`:

```nginx
server {
    listen 80;
    server_name sbms-test.example.com;

    client_max_body_size 10M;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/sbms-test /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# HTTPS через Let's Encrypt
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d sbms-test.example.com
```

---

## 4. PaaS (Railway / Render / Heroku)

### 4.1 Railway

1. `railway login && railway init`
2. `railway up` (подхватит `Procfile`)
3. В веб-консоли: **Variables** → добавить из `.env.example`:
   - `SBMS_BASE_URL`
   - `SBMS_LOGIN`, `SBMS_PASSWORD`
   - `TME_BASE_URL`
   - `TEST_MSISDN`
   - `REQUEST_TIMEOUT=30`

### 4.2 Render.com

1. Push репо на GitHub
2. New → Blueprint → подключить репо (Render прочитает `render.yaml`)
3. Заполнить секретные переменные (`SBMS_LOGIN`, `SBMS_PASSWORD`, `TEST_MSISDN`)
4. Deploy

### 4.3 Heroku

```bash
heroku create sbms-test
heroku config:set SBMS_BASE_URL=https://sbms.ucell
heroku config:set SBMS_LOGIN=xxx SBMS_PASSWORD=yyy TEST_MSISDN=998...
heroku config:set TME_BASE_URL=https://tme.billing.domain
git push heroku main
heroku open
```

---

## 5. Переменные окружения

| Переменная | Обязат. | Пример |
|-----------|---------|--------|
| `SBMS_BASE_URL` | да | `https://sbms.ucell` |
| `SBMS_LOGIN` | да | `DBS_CC_OPERATORS_PSO` |
| `SBMS_PASSWORD` | да | `Ucell2026$$` |
| `TME_BASE_URL` | нет | `https://tme.billing.domain` (дефолт) |
| `TEST_MSISDN` | нет | `998500173054` |
| `REQUEST_TIMEOUT` | нет | `30` (сек) |
| `SERVER_PORT` | нет | `5000` |
| `FLASK_DEBUG` | нет | `0` прод / `1` dev |
| `PORT` | PaaS | задаётся автоматически Heroku/Railway |

---

## 6. Чек-лист перед деплоем

- [ ] `.env` **не коммитится** (проверь `git status` — должен игнорироваться)
- [ ] Секреты заданы через переменные окружения платформы
- [ ] Пароли из `.env` заменены на продакшн-значения
- [ ] `debug=False` на проде (задаётся через отсутствие `FLASK_DEBUG=1`)
- [ ] Порт `5000` проброшен (или Nginx/PaaS маршрутизирует на него)
- [ ] `test_history/` монтируется как volume (иначе теряется при перезапуске)
- [ ] `DISPID.xlsx` присутствует (либо в образе, либо volume-mount'ом)
- [ ] HTTPS настроен (Let's Encrypt / CloudFlare / платформа)

---

## 7. Проверка после деплоя

```bash
# 1. Главный дашборд
curl -I https://your-domain/

# 2. Конфиг
curl https://your-domain/api/config

# 3. SBMS auth
curl -X POST https://your-domain/api/auth \
    -H "Content-Type: application/json" \
    -d '{"login":"...","password":"..."}'

# 4. TME auth (новая фича)
curl -X POST https://your-domain/api/tme/auth \
    -H "Content-Type: application/json" \
    -d '{"username":"...","password":"..."}'
```

Если все четыре возвращают осмысленный JSON — деплой успешен.

---

## 8. Безопасность (ВАЖНО)

Текущий `.env` в репозитории содержит **реальный пароль** — перед публикацией:

1. Убедись, что `.env` НЕ отправлен в Git:
   ```bash
   git rm --cached .env
   git commit -m "remove .env from tracking"
   ```
2. **Смени пароль** в SBMS, если `.env` уже был в публичном репо
3. Добавь `.gitignore` в коммит (уже подготовлен)
4. На проде используй платформенные секреты (Railway Variables / Render Env / Docker secrets)
5. Подумай про аутентификацию самого дашборда (сейчас он открыт всем, кто знает URL) — можно добавить Basic Auth через Nginx или Flask middleware
