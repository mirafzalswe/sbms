# SBMS Test Framework — CLAUDE.md

## Цель проекта

Автоматизированный тестовый стенд и прокси-сервер для **UCELL SBMS API** (биллинг сотового оператора). Решает три задачи:

1. **CORS-прокси** — Flask-сервер проксирует вызовы к `https://sbms.ucell`, чтобы HTML-дашборды могли обращаться к API из браузера.
2. **Интерактивные дашборды** — `dashboard.html`, `tester.html`, `tariff_test.html`, `tme.html` для ручной проверки сценариев: поиск абонента, баланс, активные пакеты/услуги, смена тарифа, остатки.
3. **Автотесты сценариев** — `TestRunner` (`sbms_runner.py`) прогоняет сценарии (смена тарифа, активация/деактивация пакетов и услуг) с pre/post-проверками и сохраняет отчёты в `test_history/`.

Используется для QA биллинг-операций (смена тарифного плана, подключение/отключение пакетов и услуг), валидации ответов API и отладки расхождений между OAPI, PSAPI и PSIX.

## Архитектура

- `server.py` — Flask прокси + REST-эндпоинты (`/api/*`) + статические HTML-страницы. Кеширует `authToken` (TTL ~25 мин).
- `sbms_client.py` — `SBMSClient`: HTTP-обёртка над SBMS API (auth, customer, balance, ratePlans, packs, services, lifecycle, PSIX).
- `sbms_checks.py` — модели `TestReport`/`TestStep`, компараторы, извлечение объёмов (`extract_volumes`), сериализация отчётов.
- `sbms_runner.py` — `TestRunner`: оркестрация сценариев (pre-snapshot → действие → ожидание → post-snapshot → diff).
- `discount_mapper.py` + `DISPID.xlsx` — маппинг `discountPlanId → человекочитаемое описание` (2041+ записей).
- `test_history/` — JSON-отчёты последних прогонов.
- `docs/` — расширенная документация (анализ API, гайды, отчёты).

## Endpoints сервера (Flask)

| Метод | Путь | Назначение |
|---|---|---|
| GET | `/` | `dashboard.html` |
| GET | `/tester` | `tester.html` — QA страница |
| GET | `/tariff-test` | `tariff_test.html` — стенд смены тарифа |
| GET | `/matrix-test` | `matrix_test.html` — матрица переходов (admin меняет → viewer проверяет доступные) |
| GET | `/tme` | `tme.html` — TME (Trouble Management Engine) |
| ANY | `/proxy/<path>` | прозрачный прокси к `SBMS_BASE_URL` |
| POST | `/api/auth` | авторизация, возврат `authToken` |
| POST | `/api/tme/auth` | авторизация в TME |
| GET | `/api/config` | базовая конфигурация (без пароля) |
| POST | `/api/test/run` | запустить сценарий (`TestRunner`) |
| GET | `/api/test/history` | список отчётов |
| GET | `/api/test/history/<id>` | один отчёт |
| POST | `/api/tariff/load-customer` | поиск абонента + баланс + доступные тарифы |
| POST | `/api/tariff/run` | сценарий смены тарифа |
| POST | `/api/pack/run` | сценарий активации/деактивации пакета |
| POST | `/api/debug/subscriptions` | дамп активных подписок |
| POST | `/api/packs/activate` · `/api/packs/deactivate` | пакеты |
| POST | `/api/services/activate` · `/api/services/deactivate` | услуги |
| POST | `/api/limits/all` | агрегация остатков (rtDiscounts + next charges) |
| POST | `/api/matrix/parse` | распарсить файл матрицы (PDF/DOCX/XLSX/CSV/JSON) → каноничный JSON |
| POST | `/api/matrix/run` | прогон матрицы (синхронный) |
| POST | `/api/matrix/run-stream` | прогон с SSE-стримом прогресса по строкам |
| GET | `/api/matrix/history` · `/api/matrix/history/<id>` | отчёты матрицы |

## SBMS API — ключевые запросы

### Авторизация
```
GET /OAPI/v1/tokens-stub/get?login=XXX&password=YYY
→ XML: <SELFCARE><SESSION_ID>...</SESSION_ID></SELFCARE>
```
Токен передаётся в остальных запросах как `?authToken=<SESSION_ID>`. Сессия живёт ~30 мин.

### Поиск абонента
```
GET /OAPI/v1/customers/searchBase?identification=<MSISDN>&authToken=...
→ searchResults[0] = {
    customerId, subscriberId,
    firstSubscriber.ratePlan.ratePlanId      ← текущий тариф (вложенный!)
  }
```

### Баланс
```
GET /PSAPI/v1/bis-base/customers/{cid}/availableBalance?authToken=...
→ { "availableBalance": <число> }            ← поле именно availableBalance
```

### Текущие остатки пакетов (минуты/SMS/MB/бонусы)
```
GET /PSAPI/v1/bis-brt-balance/subscribers/{sid}/rtDiscounts?authToken=...&customerDatabaseId=902
→ items: [ { productId, measureUnitId, discountType, remainingValue, ... } ]
```
Маппинг `measureUnitId`: **0=сум, 1=минуты, 7=SMS, 14=MB**.
Маппинг `discountType`: **4=Пакетная, 6=Безлимит, 11=Дневная, 12=Бонусная**.

⚠️ После смены тарифа в ответе присутствуют скидки от **обоих** тарифов — фильтровать по `productId`.
⚠️ `subscriptionId` из `nextCharges` ≠ `productId` в `rtDiscounts` — вычислять актуальные `productId` через diff до/после смены.

### Доступные тарифные планы
```
POST /OAPI/v1/cbss/subscribers/{sid}/ratePlans/availableForChange/search
  ?languageId=1&limit=500&offset=0&returnCount=1&showFees=1&authToken=...
→ items: [ { ratePlan: { ratePlanId, name }, isArchived, recurringFlag, ... } ]
```
⚠️ Поля **вложены в `ratePlan`**, не на верхнем уровне. `/api/tariff/load-customer` расплющивает их для фронтенда.

### Абонплата следующего периода для тарифа
```
GET /OAPI/v1/subscribers/{sid}/subscriptions/nextCharges/ratePlans/{rpId}?authToken=...
→ nextRatePlanCharges[0].recurringCharges[0].amount
```

### Смена тарифа
```
POST /OAPI/v1/subscribers/{sid}/ratePlans/change
  ?languageId=1&newRatePlanId=<rpId>&authToken=...
→ HTTP 202 + { ratePlanOrderId: ... }       ← асинхронно
```
Статус отдельного заказа (`/orders/{orderId}`) часто возвращает `null` — использовать общий список `/orders` и фильтровать по ID.

### Предпроверка смены тарифа
```
POST /OAPI/v1/subscribers/{sid}/ratePlans/change/check
  ?languageId=1&newRatePlanId=<rpId>&isFullDiscountsInfo=true&authToken=...
```

### Пакеты
```
GET  /OAPI/v1/subscribers/{sid}/packs                                  — активные
GET  /PSAPI/v1/bis-base/subscribers/{sid}/packs/availableForActivate   — доступные
POST /OAPI/v1/subscribers/{sid}/packs/activate?packId=...              body: {"actionParameters":{}}
POST /OAPI/v1/subscribers/{sid}/packs/{packInstanceId}/deactivate      body: {"actionParameters":{}}
GET  /OAPI/v1/subscribers/{sid}/subscriptions/nextCharges/packs/{packId}
```

### Услуги
```
GET  /OAPI/v1/subscribers/{sid}/services
GET  /PSAPI/v1/bis-base/subscribers/{sid}/services/availableForActivate
POST /OAPI/v1/subscribers/{sid}/services/activate?serviceId=...
POST /OAPI/v1/subscribers/{sid}/services/{serviceInstanceId}/deactivate
```

### Lifecycle
```
POST /PSAPI/internal/v1/licy-base-private/subslcstates/actual/{sid}
POST /PSAPI/internal/v1/licy-base-private/customers/{cid}/subscribers/lifeCycleInfo/search
```

### PSIX (устаревший XML/JSON, часто с пустым телом)
```
GET /PSIX/scli/UCELL_NEXT_TIME_FEE?SESSION_ID=...&P_SUBS_ID={sid}
GET /PSIX/scli/UCELL_DISCOUNTS_INFO?SESSION_ID=...&P_SUBS_ID={sid}&SUBSCRIBER_MSISDN=...
```
⚠️ PSIX может отдавать HTTP 200 с пустым телом — `.json()` оборачивать в `try/except`.

## Модель авторизации (UI + backend)

Централизованный модуль `static/sbms-auth.js` (`window.SbmsAuth`) — единственный источник токена на фронтенде.

- **Состояние хранится так:** логин → `localStorage` (удобство), токен + `expiresAt` → `sessionStorage` (живут в пределах вкладки, автоматически очищаются при закрытии).
- **Поля логина/пароля всегда пустые** на старте; логин заполняется из `localStorage`, пароль — никогда.
- **Вход:** `SbmsAuth.authenticate(login, password)` → POST `/api/auth` → получаем `{token, expiresIn, expiresAt, login}` → сохраняем в `sessionStorage`.
- **Бейдж сессии** (`SbmsAuth.mountBadge(selector)`) показывает статус и обратный отсчёт. Клик по бейджу: если залогинен — выйти, иначе — модалка входа.
- **Все запросы** берут токен из `SbmsAuth.token`: `proxyUrl(path, params)` / `apiPost` / `apiGet` / `proxyFetch` автоматически добавляют `authToken`.
- **401/403** от сервера → `SbmsAuth.clearSession('expired')` + модалка повторного входа.
- **Явный `logout`** очищает `sessionStorage` (логин в `localStorage` остаётся).
- **Backend `/api/auth`**: требует login+password в теле (без env-фолбэка), возвращает `expiresIn`/`expiresAt`. Env-креденшиалы используются только если выставлен `SBMS_ALLOW_ENV_AUTH=1` (для CLI/автотестов).
- **`_get_client()`** кидает `AuthRequired` (HTTP 401 `AUTH_REQUIRED`), если ни `authToken`, ни credentials не переданы и кеш пуст.

## Конфигурация (`.env`)
```
SBMS_BASE_URL=https://sbms.ucell
SBMS_LOGIN=DBS_CC_OPERATORS_PSO
SBMS_PASSWORD=Ucell2026$$
TME_BASE_URL=https://tme.billing.domain
TEST_MSISDN=998500173054
REQUEST_TIMEOUT=30
SERVER_PORT=5000
```

## Запуск
```bash
pip install -r requirements.txt
python server.py                  # http://localhost:5000
python run_tests.py               # консольный прогон 6 базовых тестов
```

## Частые подводные камни

- **HTTP 202 ≠ готово.** Смена тарифа асинхронная — после POST опрашивать `/orders` и `rtDiscounts`, пока новый `productId` не появится.
- **`availableBalance` vs `availableAmount`** — в новой версии поле `availableBalance`; в коде есть fallback на старое имя.
- **Вложенные структуры** — `ratePlan.ratePlanId`, `firstSubscriber.ratePlan.ratePlanId`, `nextRatePlanCharges[0].recurringCharges[0].amount`. Не путать с плоскими полями.
- **Смесь тарифов в rtDiscounts** после смены — всегда фильтровать результат по актуальному `productId`.
- **Токен живёт ~30 мин** — сервер кеширует 25 мин. Приходящий `authToken` в теле запроса имеет приоритет над кешем.
- **Отключённая верификация SSL** (`verify=False`) + подавление `InsecureRequestWarning` — SBMS использует самоподписанный сертификат.
