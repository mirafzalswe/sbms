  # 📊 ПОЛНЫЙ АНАЛИЗ ПРОЕКТА SBMS (ИСПРАВЛЕННЫЙ)

## 🏗️ Архитектура проекта

```
Клиент (браузер)
    ↓
Flask Proxy Server (server.py)
    ├─ /api/limits/all      ← rtDiscounts (остатки пакетов)
    ├─ /api/subscriber/quota ← PSIX квоты (бонусы/счётчики)
    └─ /proxy/*              ← прозрачный проксирование к SBMS API
    ↓
SBMS API (https://sbms.ucell)
    ├─ /OAPI/v1/...         ← основные операции
    ├─ /PSAPI/v1/...        ← биллинг, остатки
    └─ /PSIX/...            ← легаси XML/JSON (квоты, события)
```

---

## ⚡ Единицы измерения (КРИТИЧНО)

### Универсальное маппинг (используется везде)

```
measureUnitId:
  0  = денежные единицы (сум)
  1  = МИНУТЫ (голосовые)
  7  = SMS (текстовые сообщения)
  14 = МБ (мегабайты, интернет трафик)
```

**Где это определено:**
- `sbms_checks.py:23` — MEASURE_UNITS словарь
- `server.py:1823-1828` — UNITS в `/api/limits/all`
- `templates/subscriber.html:977-1002` — KPI карточки

**✓ ПРОВЕРКА: везде единицы ПРАВИЛЬНЫЕ**

```python
# sbms_checks.py:23
MEASURE_UNITS = {0: "сум", 1: "минуты", 7: "SMS", 14: "МБ"}  ✓

# server.py:1823-1828 (в /api/limits/all)
UNITS = {
    0:  {"label": "Деньги",   "unit": "сум"},     ✓
    1:  {"label": "Минуты",   "unit": "мин"},     ✓
    7:  {"label": "SMS",      "unit": "sms"},     ✓
    14: {"label": "Интернет", "unit": "МБ"},      ✓
}

# sbms_checks.py:205-212 (в extract_volumes)
if uid == 1:
    totals["minutes"] += max_vol              ✓ Правильно!
elif uid == 7:
    totals["sms"] += max_vol                  ✓ Правильно!
elif uid == 14:
    totals["mb"] += max_vol                   ✓ Правильно!
```

---

## 📍 ДВА ОСНОВНЫХ ПУТИ ПОЛУЧЕНИЯ ЛИМИТОВ

### 1️⃣ RTDISCOUNTS — `/api/limits/all` (Остатки пакетов текущего тарифа)

**Файл:** `server.py:1745-1878`

**Эндпоинт:** `POST /api/limits/all`

**Поток:**

```
Входные данные:
  { msisdn: "998500123456", authToken?: "..." }

1️⃣ Поиск абонента
  SBMSClient.search_customer(msisdn)
  → GET /OAPI/v1/customers/searchBase?identification=998500123456
  → Получаем: customerId, subscriberId, текущий ratePlanId

2️⃣ Текущие остатки пакетов (rtDiscounts)
  SBMSClient.get_rt_discounts(subscriberId)
  → GET /PSAPI/v1/bis-brt-balance/subscribers/{sid}/rtDiscounts?customerDatabaseId=902
  
  Ответ API содержит массив items:
  [
    {
      productId,          ← ID тарифа/пакета/услуги
      measureUnitId,      ← 0|1|7|14
      maxVolume,          ← максимальный объём
      spentVolume,        ← израсходовано
      remainingValue,     ← остаток (поле ИЗ API)
      discountPlanId,     ← ID скидки (→ маппируется через DISPID.xlsx)
      discountName,
      discountType,       ← 4=Пакетная, 6=Безлимит, 11=Дневная, 12=Бонусная
      startDate,
      endDate
    },
    ...
  ]

3️⃣ Определение источников (тариф/пакет/услуга)
  Для каждого productId определяем: это из тарифа, пакета или услуги?
  
  a) Текущий тариф:
     SBMSClient.get_rateplan_next_charges(sid, ratePlanId)
     → GET /OAPI/v1/subscribers/{sid}/subscriptions/nextCharges/ratePlans/{rpId}
     → extract_product_id_from_charges() → productId текущего тарифа
  
  b) Активные пакеты:
     SBMSClient.get_active_packs(sid)
     → GET /OAPI/v1/subscribers/{sid}/packs
     → для каждого: get_pack_next_charges() → productId пакета
  
  c) Активные услуги:
     SBMSClient.get_active_services(sid)
     → GET /OAPI/v1/subscribers/{sid}/services
     → для каждого: get_service_next_charges() → productId услуги

4️⃣ Группировка по productId
  Берём items из rtDiscounts → группируем по productId
  Для каждой группы заранее знаем: это тариф, пакет или услуга

5️⃣ Маппинг описаний скидок
  Для каждого discountPlanId:
    get_discount_description(id)
    → ищет в data/DISPID.xlsx (2041 запись)
    → возвращает human-readable описание типа "Messa 2000"

6️⃣ Вычисление остатка
  remaining = maxVolume - spentVolume
  (вычисляется в backend, если его нет в ответе API)

Выходные данные:
  {
    groups: [
      {
        productId,
        sourceName,        ← "Messa 2000" | "Интернет +5GB" | "Услуга X"
        sourceType,        ← "tariff" | "pack" | "service" | "unknown"
        discounts: [
          {
            measureUnitId,     ← 0|1|7|14
            label,             ← "Минуты" | "SMS" | "Интернет" | "Деньги"
            unit,              ← "мин" | "sms" | "МБ" | "сум"
            maxVolume,
            spentVolume,
            remaining,         ← вычислено: maxVolume - spentVolume
            discountPlanId,
            discountName,      ← из Excel маппинга
            startDate,
            endDate,
            discountType
          },
          ...
        ]
      },
      ...
    ],
    totalItems           ← всего items из rtDiscounts
  }
```

**Использование в Frontend:**
- `templates/subscriber.html:2612` — `loadLimits()` отправляет запрос
- `templates/subscriber.html:2417` — `_renderLimitsKpi()` рендерит KPI-карточки (суммы по типам)
- `templates/subscriber.html:2583` — `_renderLimitsTable()` рендерит таблицу

---

### 2️⃣ PSIX QUOTAS — `/api/subscriber/quota` (Все квоты/бонусы абонента)

**Файл:** `server.py:2084-2248`

**Эндпоинт:** `POST /api/subscriber/quota`

**Поток:**

```
Входные данные:
  { msisdn: "998500123456", authToken?: "..." }

1️⃣ Поиск абонента
  SBMSClient.search_customer(msisdn)
  → GET /OAPI/v1/customers/searchBase?identification=998500123456
  → Получаем subscriberId

2️⃣ Запрос всех квот (PSIX)
  SBMSClient.get_subscriber_all_quota(subscriberId, msisdn)
  
  Внутри:
  a) Резолв IMSI из subscriber identification
     GET /OAPI/v1/subscribers/{sid}?fields=...mainSIMCard
     → получаем IMSI (International Mobile Subscriber Identity)
  
  b) Запрос к PSIX:
     GET /PSIX/ucell/UCELL_GET_SUBSCRIBER_ALL_QUOTA
         ?IMSI=434054443280878
         &MSISDN=998500061400
         &SESSION_ID=<authToken>
  
  c) Парсинг XML ответа (может быть и JSON)
     <SELFCARE>
       <UCELL_GET_SUBSCRIBER_ALL_QUOTA>
         <PCRF_SUBSCRIBER_GET>
           <SUBSCRIBER_QUOTA>
             <QTANAME>Q_Un_Soc_Msg</QTANAME>          ← техническое имя
             <QTAVALUE>2147483648</QTAVALUE>          ← максимум
             <QTACONSUMPTION>426805</QTACONSUMPTION>  ← использовано
             <QTABALANCE>2147056843</QTABALANCE>      ← остаток
             <QTASTARTDATETIME>2026-05-06T16:11:33</QTASTARTDATETIME>
             <QTARSTDATETIME>-1--T::</QTARSTDATETIME> ← дата обнуления (или маркер бессрочности)
             <SRVNAME>S_Un_Soc_Msg</SRVNAME>
           </SUBSCRIBER_QUOTA>
           ...
         </PCRF_SUBSCRIBER_GET>
       </UCELL_GET_SUBSCRIBER_ALL_QUOTA>
     </SELFCARE>

3️⃣ Нормализация полей (поддержка 10+ алиасов)
  
  Функция: _pick_quota_field(item, key)
  
  Поддерживаемые алиасы:
    "name":     QTANAME, QUOTA_NAME, P_QUOTA_NAME, QuotaName, ...
    "total":    QTAVALUE, VOLUME, TOTAL_VOLUME, P_VOLUME, ...
    "used":     QTACONSUMPTION, USED_VOLUME, SPENT_VOLUME, ...
    "rem":      QTABALANCE, REMAINDER, REMAINING_VOLUME, ...
    "act_date": QTASTARTDATETIME, ACTIVATION_DATE, START_DATE, ...
    "exp_date": QTARSTDATETIME, EXPIRATION_DATE, END_DATE, ...

4️⃣ Классификация по категориям
  
  Функция: _classify_quota(name) [server.py:1929]
  
  Regex-паттерны (server.py:1904-1911):
    "social"   ← содержит "soc", "facebook", "instagram", "whatsapp", "telegram", "tiktok", "imo", "youtube"
    "internet" ← содержит "inet", "net", "gprs", "data", "mb", "gb", "web", "traffic", "trf"
    "voice"    ← содержит "voice", "min", "call", "onnet", "offnet", "intl", "ucell", "interconnect" ✓ "min"!
    "sms"      ← содержит "sms", "msg"
    "bonus"    ← содержит "bonus", "gift", "promo", "loyalty", "free", "extra"
    "other"    ← всё остальное

5️⃣ Определение единицы измерения
  
  Функция: _quota_unit_from_name(name) [server.py:1938]
  
  Regex-паттерны (server.py:1918-1923):
    "pcs" ← соц. сообщения (Q_Un_Soc_Msg)
    "MB"  ← mb|gb|inet|gprs|net|traffic|data|trf
    "min" ← min|voice|call|onnet|offnet|intl|interconnect ✓
    "sms" ← sms|msg ✓
    "pcs" ← count|cnt|piece|times

  ⚠️ ПОРЯДОК ВАЖЕН! Соц. сообщения проверяются ДО общего msg→sms,
     чтобы Q_Un_Soc_Msg не стал SMS, а остался штуками.

6️⃣ Humanize technical names
  
  Функция: _humanize_quota_name(name) [server.py:1947]
  
  Лексикон (server.py:1955-1979):
    "un"   → "безлимит"
    "soc"  → "соц. сети"
    "msg"  → "сообщения"
    "net"  → "трафик"
    "inet" → "интернет"
    "gprs" → "интернет"
    "data" → "интернет"
    "mb"   → "МБ"
    "gb"   → "ГБ"
    "min"  → "минуты"           ✓ ПРАВИЛЬНО!
    "voice" → "голос"
    "call" → "звонки"
    "sms"  → "SMS"              ✓ ПРАВИЛЬНО!
    "onnet"  → "Ucell"
    "offnet" → "другие сети"
    "intl"   → "межгород"
    "bonus"  → "бонус"
    "free"   → "бесплатно"
    "promo"  → "промо"
    "fb"     → "Facebook"
    "ig"     → "Instagram"
    "wa"     → "WhatsApp"
    "tg"     → "Telegram"
  
  Пример: Q_Un_Soc_Msg → "безлимит · соц. сети · сообщения"

7️⃣ Парсинг дат и маркеров бессрочности
  
  Функция: _norm_quota_date(value) [server.py:1987]
  
  Маркеры БЕССРОЧНОСТИ (возвращает None):
    "01.01.0001 [HH:MM:SS]"  ← старый формат
    "0001-01-01[T...]"       ← ISO
    "-1--T::"                ← PSIX формат (см. реальный дамп)
    "9999-*" | "2999-*"      ← бесконечно-в-будущем
  
  Остальное парсится в ISO-формат: "2026-05-06T16:11:33"

8️⃣ Вычисление статуса
  
  Логика (server.py:2186-2203):
    Если total > 0 и remaining ≤ 0:
      status = "exhausted"           ← исчерпана
    Иначе если unlimited (дата была, но это 01.01.0001):
      status = "unlimited"           ← бессрочная
    Иначе если есть дата обнуления:
      Если expiration_date < now:
        status = "inactive"          ← истекла
      Иначе если expiration_date ≤ now + 3 дня:
        status = "expiring"          ← вот-вот истечёт
      Иначе:
        status = "active"            ← активна
    Иначе:
      status = "active"

9️⃣ Вычисление процента использования
  
  percent = (used / total) * 100
  
  Если used не задано: used = total - remaining

🔟 Вывод результата
  
  Выходные данные:
  {
    items: [
      {
        name,                ← "Q_Un_Soc_Msg"
        nameHuman,           ← "безлимит · соц. сети · сообщения"
        service,             ← "S_Un_Soc_Msg"
        category,            ← "social" | "internet" | "voice" | "sms" | "bonus" | "other"
        unit,                ← "MB" | "min" | "sms" | "pcs" | ""
        total,               ← QTAVALUE
        used,                ← QTACONSUMPTION
        remaining,           ← QTABALANCE или total - used
        percent,             ← (used / total) * 100
        activationDate,      ← ISO или null
        expirationDate,      ← ISO или null
        unlimited,           ← bool
        status,              ← "active" | "expiring" | "exhausted" | "inactive" | "unlimited"
        raw                  ← оригинальный item из API
      },
      ...
    ],
    groups: {
      internet: [...],
      sms:      [...],
      voice:    [...],
      social:   [...],
      bonus:    [...],
      other:    [...]
    },
    count,
    subscriberId,
    fetchedAt
  }
  
  Сортировка:
    1) По статусу: expiring → active → exhausted → unlimited → inactive
    2) Внутри статуса: по % использования (descending)
    3) Внутри того же % — по имени
```

**Использование в Frontend:**
- `templates/subscriber.html:2667` — `loadQuotas()` отправляет запрос
- `templates/subscriber.html:2720` — `_renderQuotasTable()` рендерит таблицу

---

## 🔗 Backend функции для работы с объёмами

### `sbms_checks.py`

#### `extract_volumes(rt_discounts_data)` — Линия 192-231

Агрегирует все лимиты по типам:

```python
def extract_volumes(rt_discounts_data):
    """Извлечь объёмы из ответа rtDiscounts."""
    items = rt_discounts_data.get("items", [])
    
    totals = {"minutes": 0, "sms": 0, "mb": 0, "money": 0}
    details = []
    
    for item in items:
        uid = item.get("measureUnitId", -1)
        max_vol = item.get("maxVolume", 0) or 0
        spent = item.get("spentVolume", 0) or 0
        remaining = max_vol - spent
        
        if uid == 1:
            totals["minutes"] += max_vol        ✓ ПРАВИЛЬНО
        elif uid == 7:
            totals["sms"] += max_vol            ✓ ПРАВИЛЬНО
        elif uid == 14:
            totals["mb"] += max_vol
        elif uid == 0:
            totals["money"] += max_vol
        
        details.append({...})
    
    return totals, details
```

#### `extract_volumes_by_product_id(rt_discounts_data, product_id)` — Линия 234-279

Фильтрует лимиты только для одного productId (нужно после смены тарифа):

```python
def extract_volumes_by_product_id(rt_discounts_data, product_id):
    """После смены тарифа в rtDiscounts могут быть items от СТАРОГО и НОВОГО.
    Эта функция фильтрует только по productId текущего тарифа."""
    
    items = rt_discounts_data.get("items", [])
    filtered = [item for item in items if item.get("productId") == product_id]
    
    # Далее логика как в extract_volumes()
    return totals, details, count
```

---

## 🎨 Frontend отрисовка

### Tab "Limits" — Остатки пакетов (rtDiscounts)

**HTML:** `templates/subscriber.html:960-1087`

**KPI блок:**
```html
<div class="lim-kpi-grid">
  <div class="lim-kpi" data-unit="1">
    <span class="lim-kpi__label">Минуты</span>
    <div class="lim-kpi__value" id="lkMinRem">—</div>
    <div class="lim-kpi__sub" id="lkMinMax">из —</div>
  </div>
  
  <div class="lim-kpi" data-unit="7">
    <span class="lim-kpi__label">SMS</span>
    <div class="lim-kpi__value" id="lkSmsRem">—</div>
    <div class="lim-kpi__sub" id="lkSmsMax">из —</div>
  </div>
  
  <div class="lim-kpi" data-unit="14">
    <span class="lim-kpi__label">Интернет</span>
    <div class="lim-kpi__value" id="lkInternetRem">—</div>
    <div class="lim-kpi__sub" id="lkInternetMax">из —</div>
  </div>
  
  <div class="lim-kpi" data-unit="0">
    <span class="lim-kpi__label">Баланс</span>
    <div class="lim-kpi__value" id="lkMoneyRem">—</div>
    <div class="lim-kpi__sub" id="lkMoneyMax">из —</div>
  </div>
</div>
```

**Функции JavaScript:**

```javascript
async function loadLimits(force) {
  // server.py:1745 — POST /api/limits/all
  const response = await apiPost("/api/limits/all", { 
    msisdn, authToken 
  });
  
  _limitsRows = response.groups.flatMap(g => 
    g.discounts.map(d => ({...}))
  );
  
  _renderLimitsKpi(_limitsRows);    // Линия 2417
  _renderLimitsTable();              // Линия 2583
}

function _renderLimitsKpi(rows) {
  // Агрегирует по measureUnitId и заполняет KPI-карточки
  const totals = { 1: {...}, 7: {...}, 14: {...}, 0: {...} };
  
  rows.forEach(r => {
    if (r.measureUnitId === 1) {
      totals[1].rem += r.remaining;
      totals[1].max += r.maxVolume;
    }
    else if (r.measureUnitId === 7) {
      totals[7].rem += r.remaining;
      totals[7].max += r.maxVolume;
    }
    // ... и т.д.
  });
  
  // Заполняем DOM элементы
  $('lkMinRem').textContent = formatNumber(totals[1].rem);
  $('lkSmsRem').textContent = formatNumber(totals[7].rem);
  // ...
}

function _renderLimitsTable() {
  // Рендерит таблицу с колонками:
  // Источник | Тип | Макс | Потрачено | Осталось | % | Период
}
```

### Tab "Quotas" — Квоты из PSIX

**HTML:** `templates/subscriber.html:1088-1177`

**Функции JavaScript:**

```javascript
async function loadQuotas(force) {
  // server.py:2084 — POST /api/subscriber/quota
  const response = await apiPost("/api/subscriber/quota", { 
    msisdn, authToken 
  });
  
  _quotasData = response;
  _renderQuotasTable();  // Линия 2720
}

function _renderQuotasTable() {
  // Рендерит таблицу с колонками:
  // Название | Категория | Единица | Макс | Израсход. | Остаток | % (bar) | Статус | Дата обнуления
  
  // Сортировка: expiring → active → exhausted → unlimited → inactive
  // По % использования (descending)
}
```

---

## 📊 Отладочный чек-лист

### ✓ Единицы измерения везде правильные:

- [x] `sbms_checks.py:23` — MEASURE_UNITS правильно: 1=минуты, 7=SMS, 14=МБ
- [x] `server.py:1823` — UNITS в `/api/limits/all` правильно
- [x] `server.py:1904-1923` — классификация и unit_rules правильно
- [x] `sbms_checks.py:205-212` — extract_volumes() правильно распределяет по типам
- [x] `templates/subscriber.html:977-1002` — KPI карточки правильно маппят data-unit
- [x] `server.py:1955-1979` — лексикон humanize правильно переводит "min"→"минуты", "sms"→"SMS"

### ⚠️ Потенциальные проблемы (если есть):

- API может вернуть другие имена полей (поддерживается 10+ алиасов в `_pick_quota_field`)
- PSIX может возвращать пустой ответ (обработано в `get_subscriber_all_quota`)
- measureUnitId может быть неизвестным (fallback `unit_{uid}`)
- discountPlanId может отсутствовать в DISPID.xlsx (fallback — пустое имя)

---

## 📁 Ключевые файлы и строки

| Файл | Строка | Функция |
|------|--------|---------|
| **server.py** | 1745-1878 | `/api/limits/all` — основной endpoint лимитов |
| **server.py** | 1823-1828 | UNITS маппинг (0=сум, 1=мин, 7=sms, 14=МБ) |
| **server.py** | 2084-2248 | `/api/subscriber/quota` — квоты из PSIX |
| **server.py** | 1904-1923 | классификация и unit rules для квот |
| **server.py** | 1929 | `_classify_quota()` — определение категории |
| **server.py** | 1938 | `_quota_unit_from_name()` — определение единицы |
| **server.py** | 1947-1984 | `_humanize_quota_name()` — перевод tech. names |
| **server.py** | 1987-2036 | `_norm_quota_date()` — парсинг дат |
| **sbms_checks.py** | 23 | MEASURE_UNITS: 1=минуты, 7=SMS, 14=МБ |
| **sbms_checks.py** | 192-231 | `extract_volumes()` — агрегация лимитов |
| **sbms_checks.py** | 234-279 | `extract_volumes_by_product_id()` — фильтр |
| **sbms_client.py** | 380-384 | `get_rt_discounts()` → PSAPI |
| **sbms_client.py** | 901-1054 | `get_subscriber_all_quota()` → PSIX |
| **templates/subscriber.html** | 2417-2445 | `_renderLimitsKpi()` — KPI блок |
| **templates/subscriber.html** | 2583-2610 | `_renderLimitsTable()` — таблица лимитов |
| **templates/subscriber.html** | 2612-2664 | `loadLimits()` — загрузка данных |
| **templates/subscriber.html** | 2667-2719 | `loadQuotas()` — загрузка квот |
| **templates/subscriber.html** | 2720-... | `_renderQuotasTable()` — таблица квот |
| **discount_mapper.py** | — | маппинг DISPID.xlsx (2041 запись) |

---

## 🚀 Как работает UI при загрузке страницы

```
1. Пользователь заходит на /subscriber
   ↓
2. Вводит MSISDN, нажимает "Загрузить"
   ↓
3. JavaScript вызывает loadLimits():
   POST /api/limits/all { msisdn, authToken }
   ↓
4. Backend возвращает groups (группированные по productId)
   ↓
5. Frontend:
   a) _renderLimitsKpi() → показывает KPI карточки (всего мин/SMS/МБ)
   b) _renderLimitsTable() → показывает таблицу с деталями
   ↓
6. Пользователь видит две строки данных:
   - "Messa 2000" тариф → 1000 минут (500 потрачено, 500 осталось)
   - "Интернет +5GB" пакет → 5120 МБ (2048 потрачено, 3072 осталось)

7. Если переключиться на Tab "Quotas":
   POST /api/subscriber/quota { msisdn, authToken }
   ↓
8. Backend возвращает массив items с квотами
   ↓
9. Frontend _renderQuotasTable() показывает таблицу с:
   - Q_Un_Soc_Msg → "безлимит · соц. сети · сообщения"
   - Q_Inet_Bonus → "интернет · бонус"
   - и т.д.
```

---

## 💡 Выводы

1. **Единицы измерения ВЕЗДЕ ПРАВИЛЬНЫЕ** — 1=мин, 7=SMS, 14=МБ, 0=сум
2. **Два разных источника данных:**
   - rtDiscounts (быстро, из текущего пакета тарифа)
   - PSIX квоты (детально, все бонусы/счётчики с датами)
3. **Frontend показывает оба источника в разных табах**
4. **Классификация и humanize — только на backend** (нельзя доверять названиям квот)
5. **Маппинг discountPlanId → Excel файл** (2041 запись, может быть недополнен)
