# 📊 ПОЛНЫЙ АНАЛИЗ ПРОЕКТА: UCELL SBMS API Test Framework

> **Дата анализа:** 20 февраля 2026 г.  
> **Версия:** 1.0  
> **Аналитик:** AI System Analysis

---

## 🎯 ЦЕЛЬ И НАЗНАЧЕНИЕ ПРОЕКТА

### Основная цель
**Автоматизированное тестирование и валидация SBMS API** (Subscriber Billing Management System) оператора UCELL (Узбекистан).

### Бизнес-задачи
1. ✅ **QA Тестирование** - автоматическая проверка работоспособности биллинговой системы
2. ✅ **Регрессионное тестирование** - валидация после обновлений системы
3. ✅ **Тестирование смены тарифов** - критически важный процесс для абонентов
4. ✅ **Мониторинг API** - проверка доступности и корректности ответов
5. ✅ **Документация API** - живая документация с примерами запросов/ответов

---

## 🏗️ АРХИТЕКТУРА СИСТЕМЫ

```
┌─────────────────────────────────────────────────────────────┐
│                    SBMS SYSTEM (Backend)                     │
│              https://sbms.ucell (Production)                 │
│                                                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │
│  │   OAPI   │  │  PSAPI   │  │   PSIX   │  │   CDM    │   │
│  │  (JSON)  │  │  (JSON)  │  │  (XML)   │  │ (Reports)│   │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │
└─────────────────────────────────────────────────────────────┘
                              ↕
                          SSL/HTTPS
                              ↕
┌─────────────────────────────────────────────────────────────┐
│                    TEST FRAMEWORK (Python)                   │
│                                                               │
│  ┌───────────────┐  ┌───────────────┐  ┌─────────────────┐│
│  │ sbms_client   │→ │ sbms_runner   │→ │  sbms_checks    ││
│  │ (HTTP Client) │  │ (Orchestrator)│  │  (Validation)   ││
│  └───────────────┘  └───────────────┘  └─────────────────┘│
│                              ↓                                │
│                    ┌─────────────────┐                       │
│                    │    server.py    │                       │
│                    │  (Flask Proxy)  │                       │
│                    └─────────────────┘                       │
└─────────────────────────────────────────────────────────────┘
                              ↕
                           HTTP API
                              ↕
┌─────────────────────────────────────────────────────────────┐
│                  WEB INTERFACES (Browser)                    │
│                                                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │  Dashboard   │  │  QA Tester   │  │ Tariff Test  │     │
│  │   (40+ API)  │  │  (4 Types)   │  │  (Change)    │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────┘
```

---

## 📡 API ENDPOINTS И ЗАПРОСЫ

### 🔐 1. АВТОРИЗАЦИЯ

#### **GET /OAPI/v1/tokens-stub/get**
**Цель:** Получение токена сессии для всех последующих запросов

**Параметры запроса:**
```
GET https://sbms.ucell/OAPI/v1/tokens-stub/get
?login=DBS_CC_OPERATORS_PSO
&password=Ucell2026$$
```

**Формат ответа:** XML
```xml
<SELFCARE>
  <SESSION_ID>AAAI0gLupfeOAE692QI.Uhu46...</SESSION_ID>
</SELFCARE>
```

**Обработка в коде:**
```python
def authenticate(self, login, password):
    resp = self._get("/OAPI/v1/tokens-stub/get", 
                     {"login": login, "password": password})
    root = ET.fromstring(resp.text)
    el = root.find("SESSION_ID")
    self.token = el.text
```

**Использование токена:**
Все последующие запросы требуют параметр `authToken={SESSION_ID}` или `SESSION_ID={SESSION_ID}`

---

### 👤 2. ПОИСК КЛИЕНТА

#### **GET /OAPI/v1/customers/searchBase**
**Цель:** Поиск клиента по номеру телефона (MSISDN) или номеру счета

**Параметры запроса:**
```
GET https://sbms.ucell/OAPI/v1/customers/searchBase
?identification=998500173054
&authToken={TOKEN}
```

**Альтернативный поиск:**
```
?accountNumber=1451063308  # по номеру счета
```

**Формат ответа:** JSON
```json
{
  "listInfo": {"limit": 20, "offset": 0, "count": 1},
  "searchResults": [
    {
      "customerId": 275296478,
      "subscriberId": 1252275098,
      "customer": {
        "customerId": 275296478,
        "name": "RAIMOV XASAN SHERZODOVICH",
        "juralType": {"juralTypeId": 1, "name": "Физическое лицо"},
        "status": {"customerStatusId": 2, "name": "Действующий"},
        "category": {"customerCategoryId": 1, "name": "Prepaid"},
        "customerType": {"customerTypeId": 101, "name": "Коммерческий(без factura.uz)"},
        "branch": {"branchId": 13, "name": "г.Ташкент"},
        "accountNumber": "1451063308",
        "language": {"languageId": 4, "name": "Узбекский"},
        "individualName": {
          "firstName": "XASAN",
          "surname": "RAIMOV",
          "patronymic": "SHERZODOVICH"
        }
      },
      "firstSubscriber": {
        "identification": "998500173054",
        "status": {"subscriberStatusId": 2, "name": "Действующий"},
        "ratePlan": {"ratePlanId": 380, "name": "Yangi Start"},
        "zone": {"subscriberZoneId": 13, "name": "г.Ташкент"},
        "activationDate": "2025-01-03T10:50:08",
        "standard": {"standardId": 1, "name": "GSM"}
      }
    }
  ]
}
```

**Ключевые поля:**
- `customerId` - ID клиента (для запросов баланса, атрибутов)
- `subscriberId` - ID абонента (для запросов тарифов, пакетов, услуг)
- `ratePlan.ratePlanId` - текущий тариф
- `status.name` - статус абонента

**Обработка в коде:**
```python
search_data = client.search_customer(msisdn)
sr = search_data["searchResults"][0]
customer_id = sr.get("customerId")
subscriber_id = sr.get("subscriberId")
rate_plan = sr.get("firstSubscriber", {}).get("ratePlan", {})
```

---

### 💰 3. БАЛАНС И ОСТАТКИ

#### **GET /PSAPI/v1/bis-base/customers/{customerId}/availableBalance**
**Цель:** Получение текущего баланса клиента

**Параметры запроса:**
```
GET https://sbms.ucell/PSAPI/v1/bis-base/customers/275296478/availableBalance
?authToken={TOKEN}
```

**Формат ответа:** JSON
```json
{
  "customerId": 275296478,
  "availableBalance": 17603.331499,
  "reservedBalance": 0,
  "spentBalance": 0,
  "accountEnabled": true,
  "balanceType": 3,
  "virtualPayments": 984263.17,
  "synchronizedBalance": -966659.838501,
  "payments": 0,
  "commonBalance": 17603.331499,
  "currentBalance": -966659.838501,
  "conditionalBalance": 17603.331499,
  "realTimeBalanceNotFound": false
}
```

**Ключевые поля:**
- `availableBalance` - **основной баланс** (доступные средства)
- `reservedBalance` - зарезервированные средства
- `virtualPayments` - виртуальные платежи
- `accountEnabled` - счет активен

---

#### **GET /PSAPI/v1/bis-brt-balance/subscribers/{subscriberId}/rtDiscounts**
**Цель:** Получение остатков пакетов (минуты, SMS, интернет)

**Параметры запроса:**
```
GET https://sbms.ucell/PSAPI/v1/bis-brt-balance/subscribers/1252275098/rtDiscounts
?authToken={TOKEN}
&customerDatabaseId=902
&callCreditIds=4606058645  # опционально, для фильтрации
```

**Формат ответа:** JSON
```json
{
  "items": [
    {
      "discountType": 4,         // 4=пакетная, 6=безлимит, 11=дневная, 12=бонусная
      "discountPlanId": 505,
      "callCreditId": 4606058645,
      "productId": 258,          // ID продукта (тарифа/пакета)
      "measureUnitId": 14,       // 0=сум, 1=мин, 7=SMS, 14=МБ
      "spentVolume": 0,          // использовано
      "maxVolume": 5120,         // максимум (МБ)
      "reservedVolume": 0,
      "startDate": "2026-02-16T10:06:27",
      "endDate": "2026-03-15T23:59:59",
      "balanceCategory": 0
    },
    {
      "discountType": 4,
      "productId": 258,
      "measureUnitId": 1,        // минуты
      "spentVolume": 0,
      "maxVolume": 500,
      "startDate": "2026-02-16T10:06:27",
      "endDate": "2026-03-15T23:59:59"
    },
    {
      "discountType": 4,
      "productId": 258,
      "measureUnitId": 7,        // SMS
      "spentVolume": 0,
      "maxVolume": 500,
      "startDate": "2026-02-16T10:06:27",
      "endDate": "2026-03-15T23:59:59"
    },
    {
      "discountType": 6,         // безлимит
      "productId": 0,
      "measureUnitId": 0,
      "maxVolume": 999999999,
      "startDate": "2026-02-01T00:00:00",
      "endDate": "2026-03-01T00:00:00"
    }
  ]
}
```

**Единицы измерения (measureUnitId):**
```python
MEASURE_UNITS = {
    0: "сум",      # деньги
    1: "минуты",   # голосовые минуты
    7: "SMS",      # SMS сообщения
    14: "МБ"       # интернет-трафик в мегабайтах
}
```

**Типы скидок (discountType):**
- `4` - Пакетная скидка (фиксированный объем)
- `6` - Безлимит
- `11` - Дневная скидка
- `12` - Бонусная скидка

**Обработка в коде:**
```python
def extract_volumes(rt_discounts_data):
    items = rt_discounts_data.get("items", [])
    totals = {"minutes": 0, "sms": 0, "mb": 0, "money": 0}
    
    for item in items:
        uid = item.get("measureUnitId")
        max_vol = item.get("maxVolume", 0)
        
        if uid == 1:
            totals["minutes"] += max_vol
        elif uid == 7:
            totals["sms"] += max_vol
        elif uid == 14:
            totals["mb"] += max_vol
        elif uid == 0:
            totals["money"] += max_vol
    
    return totals
```

**Важно:** При смене тарифа в rtDiscounts могут быть items от РАЗНЫХ тарифов (старого и нового). Для точной фильтрации используется `productId`:

```python
def extract_volumes_by_product_id(rt_discounts_data, product_id):
    """Извлечь объёмы только для конкретного тарифа"""
    items = rt_discounts_data.get("items", [])
    filtered = [item for item in items if item.get("productId") == product_id]
    # ... агрегация по measureUnitId
```

---

### 📋 4. ТАРИФНЫЕ ПЛАНЫ (RATE PLANS)

#### **POST /OAPI/v1/cbss/subscribers/{subscriberId}/ratePlans/availableForChange/search**
**Цель:** Получение списка доступных тарифных планов для смены

**Параметры запроса:**
```
POST https://sbms.ucell/OAPI/v1/cbss/subscribers/1252275098/ratePlans/availableForChange/search
?languageId=1
&limit=500
&offset=0
&returnCount=1
&showFees=1
&authToken={TOKEN}
```

**Формат ответа:** JSON
```json
{
  "listInfo": {"limit": 500, "offset": 0, "count": 45},
  "items": [
    {
      "ratePlan": {
        "ratePlanId": 78,
        "name": "Bor 60",
        "description": "Тариф с пакетом минут и интернета",
        "isArchived": false
      },
      "recurringFlag": true,
      "allowedActions": ["CHANGE"]
    },
    {
      "ratePlan": {
        "ratePlanId": 79,
        "name": "Bor 80",
        "isArchived": false
      },
      "recurringFlag": true
    }
  ]
}
```

**Ключевые поля:**
- `ratePlan.ratePlanId` - ID тарифа для смены
- `ratePlan.name` - название тарифа
- `isArchived` - архивный тариф (недоступен для новых подключений)
- `recurringFlag` - есть ли абонентская плата

---

#### **GET /OAPI/v1/subscribers/{subscriberId}/subscriptions/nextCharges/ratePlans/{ratePlanId}**
**Цель:** Получение информации о следующих списаниях по тарифу

**Параметры запроса:**
```
GET https://sbms.ucell/OAPI/v1/subscribers/1252275098/subscriptions/nextCharges/ratePlans/78
?authToken={TOKEN}
```

**Формат ответа:** JSON
```json
{
  "nextRatePlanCharges": [
    {
      "recurringCharges": [
        {
          "chargingState": "PRESENT",  // PRESENT/ABSENT
          "amount": 60000,
          "subscriptionId": 258,       // ≠ productId!
          "startDate": "2026-02-18T14:46:54+05:00",
          "endDate": "2026-03-18T00:00:00+05:00",
          "periodId": 1,
          "periodType": "MONTH",
          "periodAlignment": "FIXED_TIME",
          "periodDuration": 1
        }
      ],
      "ratePlanId": 78
    }
  ]
}
```

**Извлечение абонентской платы:**
```python
def extract_recurring_charge(nc_data):
    """Извлечь amount из recurringCharges"""
    rc = find_recurring_charges(nc_data)  # рекурсивный поиск
    if rc and isinstance(rc, list) and len(rc) > 0:
        return rc[0].get("amount")
    return None
```

**⚠️ Важное замечание:**
`subscriptionId` из `nextCharges` **НЕ РАВЕН** `productId` из `rtDiscounts`! 
- `subscriptionId` - внутренний ID подписки на тариф
- `productId` - ID продукта в системе скидок

---

#### **POST /OAPI/v1/subscribers/{subscriberId}/ratePlans/change**
**Цель:** Смена тарифного плана абонента (асинхронная операция)

**Параметры запроса:**
```
POST https://sbms.ucell/OAPI/v1/subscribers/1252275098/ratePlans/change
?languageId=1
&newRatePlanId=78
&activationDate=          # опционально
&authToken={TOKEN}
```

**Формат ответа:** JSON (HTTP 202 Accepted)
```json
{
  "ratePlanOrderId": "o10420443418",
  "status": {
    "ratePlanOrderStatusId": 5,
    "name": "В процессе подключения"
  }
}
```

**Статусы заказа (ratePlanOrderStatusId):**
- `5` - "В процессе подключения"
- `1` - "Подключен" ✅
- `3` - "Отключен"
- `7` - "Отклонён по балансу" ❌
- `8` - "Ошибка" ❌

**Обработка в коде:**
```python
def change_rateplan(self, subscriber_id, new_rateplan_id):
    resp = self._post(
        f"/OAPI/v1/subscribers/{subscriber_id}/ratePlans/change",
        params={
            "languageId": 1,
            "newRatePlanId": new_rateplan_id,
            "authToken": self.token
        }
    )
    return resp.json()  # {"ratePlanOrderId": "...", "status": {...}}
```

---

#### **GET /OAPI/v1/subscribers/{subscriberId}/ratePlans/orders**
**Цель:** Получение истории заказов на смену тарифа

**Параметры запроса:**
```
GET https://sbms.ucell/OAPI/v1/subscribers/1252275098/ratePlans/orders
?languageId=1
&ratePlanOrderStatusIds=  # опционально, фильтр по статусам
&authToken={TOKEN}
```

**Формат ответа:** JSON
```json
{
  "listInfo": {"limit": 20, "offset": 0, "count": 116},
  "items": [
    {
      "ratePlanOrderId": "o10420443418",
      "ratePlan": {"ratePlanId": 78, "name": "Bor 60"},
      "startDate": "2026-02-18T14:46:54",
      "endDate": "2999-12-31T00:00:00",
      "changeDate": "2026-02-18T14:46:56",
      "changeUser": "MBUS",
      "subscriberComment": null,
      "status": {
        "ratePlanOrderStatusId": 7,
        "name": "Отклонён по балансу"
      },
      "operationEvents": []
    },
    {
      "ratePlanOrderId": "h112",
      "ratePlan": {"ratePlanId": 380, "name": "Yangi Start"},
      "startDate": "2026-02-16T10:06:27",
      "endDate": "2999-12-31T00:00:00",
      "changeDate": "2026-02-16T10:06:27",
      "changeUser": "TME:MIRAFZAL.BAHODIROV",
      "status": {
        "ratePlanOrderStatusId": 1,
        "name": "Подключен"
      }
    }
  ]
}
```

**Использование для опроса статуса:**
```python
# После change_rateplan ждем и проверяем статус
for i in range(max_polls):
    time.sleep(poll_interval)
    all_orders = client.get_rateplan_orders(subscriber_id)
    for order in all_orders.get("items", []):
        if str(order.get("ratePlanOrderId")) == str(order_id):
            status_id = order.get("status", {}).get("ratePlanOrderStatusId")
            if status_id != 5:  # не "В процессе"
                return order
```

---

### 📦 5. ПАКЕТЫ (PACKS)

#### **GET /OAPI/v1/subscribers/{subscriberId}/packs**
**Цель:** Получение списка активных пакетов абонента

**Параметры запроса:**
```
GET https://sbms.ucell/OAPI/v1/subscribers/1252275098/packs
?authToken={TOKEN}
```

**Формат ответа:** JSON
```json
{
  "items": [
    {
      "packInstanceId": "123456789",
      "pack": {
        "packId": 1001,
        "name": "Интернет 10 ГБ",
        "description": "Дополнительный интернет-пакет"
      },
      "status": {
        "packInstanceStatusId": 1,
        "name": "Активен"
      },
      "activationDate": "2026-02-15T10:00:00",
      "deactivationDate": "2026-03-15T23:59:59"
    }
  ]
}
```

---

#### **GET /PSAPI/v1/bis-base/subscribers/{subscriberId}/packs/availableForActivate**
**Цель:** Получение списка доступных для активации пакетов

**Параметры запроса:**
```
GET https://sbms.ucell/PSAPI/v1/bis-base/subscribers/1252275098/packs/availableForActivate
?authToken={TOKEN}
&limit=500
&unlimited=1
&offset=0
```

**Формат ответа:** JSON
```json
{
  "items": [
    {
      "packId": 1001,
      "name": "Интернет 10 ГБ",
      "description": "10 ГБ на месяц",
      "fee": 50000,
      "duration": 30
    }
  ]
}
```

---

#### **POST /OAPI/v1/subscribers/{subscriberId}/packs/activate**
**Цель:** Активация пакета для абонента

**Параметры запроса:**
```
POST https://sbms.ucell/OAPI/v1/subscribers/1252275098/packs/activate
?authToken={TOKEN}
&packId=1001
```

**Body:** JSON
```json
{
  "actionParameters": []
}
```

---

### 🔧 6. УСЛУГИ (SERVICES)

#### **GET /OAPI/v1/subscribers/{subscriberId}/services**
**Цель:** Получение списка активных услуг абонента

**Параметры запроса:**
```
GET https://sbms.ucell/OAPI/v1/subscribers/1252275098/services
?authToken={TOKEN}
&fields=  # опционально
&limit=   # опционально
```

**Формат ответа:** JSON
```json
{
  "items": [
    {
      "serviceInstanceId": "987654321",
      "serviceId": 2001,
      "name": "Caller ID",
      "status": {"name": "Активна"},
      "activationDate": "2025-01-05T12:00:00"
    }
  ]
}
```

---

#### **GET /PSAPI/v1/bis-base/subscribers/{subscriberId}/services/availableForActivate**
**Цель:** Получение списка доступных для активации услуг

---

### 📝 7. АТРИБУТЫ КЛИЕНТА

#### **GET /OAPI/v1/customers/{customerId}/currentCustomAttributes**
**Цель:** Получение пользовательских атрибутов клиента (паспорт, ПИН, etc.)

**Параметры запроса:**
```
GET https://sbms.ucell/OAPI/v1/customers/275296478/currentCustomAttributes
?authToken={TOKEN}
```

**Формат ответа:** JSON
```json
{
  "items": [
    {
      "customAttributeId": 20,
      "name": "Дата окончания действия паспорта",
      "dataType": "DATE",
      "values": [
        {"value": "2030-12-31"}
      ]
    },
    {
      "customAttributeId": 33,
      "name": "ПИН ФЛ",
      "dataType": "STRING",
      "values": [
        {"value": "31705985950024"}
      ]
    },
    {
      "customAttributeId": 553,
      "name": "Признак подключения через МП",
      "dataType": "DICTIONARY",
      "values": [
        {"value": {"id": 1, "name": "Да"}}
      ]
    }
  ]
}
```

**Ключевые атрибуты:**
- `customAttributeId=20` - Дата окончания паспорта
- `customAttributeId=33` - ПИН физического лица
- `customAttributeId=553` - Подключение через мобильное приложение

---

### 📞 8. ОБРАЩЕНИЯ (INQUIRIES)

#### **POST /OAPI/v1/inquiries/slaveCustomProperties**
**Цель:** Получение зависимых свойств обращений (для формы создания обращения)

**Параметры запроса:**
```
POST https://sbms.ucell/OAPI/v1/inquiries/slaveCustomProperties
?masterCustomPropertyDeclarationId=1045
&AuthToken={TOKEN}
```

**Body:** JSON
```json
{
  "inquiry": {
    "topic": {
      "topicId": 365,
      "topicCode": "UCELL:CONTRACT:FO:B2C"
    },
    "priority": {
      "inquiryPriorityId": 1,
      "inquiryPriorityCode": "LOW"
    },
    "customProperties": [
      {
        "customPropertyDeclaration": {
          "customPropertyDeclarationId": 1045,
          "customPropertyDeclarationCode": "UCELL:CONTACT:TYPE"
        },
        "type": "DICTIONARY",
        "values": []
      },
      {
        "customPropertyDeclaration": {
          "customPropertyDeclarationId": 1046,
          "customPropertyDeclarationCode": "UCELL:CONTRACT:GROUP"
        },
        "type": "DB_QUERY",
        "values": [{"value": "DMS_FO_B2C_ALL"}]
      }
    ]
  },
  "contact": {
    "direction": "INCOMING",
    "contactSite": {"contactSiteId": 21, "contactSiteCode": "OFFICE_CONTACT"},
    "channel": {"channelId": 2, "channelCode": "VISIT"}
  }
}
```

**Формат ответа:** JSON
```json
{
  "customPropertyValues": [
    {
      "id": 1,
      "name": "Тип обращения",
      "values": ["Жалоба", "Вопрос", "Предложение"]
    }
  ],
  "customPropertyAccessibilities": [
    {
      "customPropertyDeclarationId": 1045,
      "name": "Тип обращения",
      "accessibility": "MANDATORY"
    },
    {
      "customPropertyDeclarationId": 1048,
      "name": "Категория",
      "accessibility": "MANDATORY"
    },
    {
      "customPropertyDeclarationId": 1050,
      "name": "Дополнительное поле",
      "accessibility": "HIDDEN"
    }
  ]
}
```

**Типы доступности:**
- `MANDATORY` - обязательное поле (*)
- `OPTIONAL` - опциональное поле (o)
- `HIDDEN` - скрытое поле (-)

---

### 📱 9. СВОБОДНЫЕ НОМЕРА

#### **GET /OAPI/v1/networkResources/phoneNumbers/free**
**Цель:** Получение списка свободных номеров для подключения

**Параметры запроса:**
```
GET https://sbms.ucell/OAPI/v1/networkResources/phoneNumbers/free
?authToken={TOKEN}
&standardId=1
&freeMonthPeriod=2
```

**Параметры:**
- `standardId=1` - GSM
- `freeMonthPeriod=2` - свободны минимум 2 месяца

**Формат ответа:** JSON
```json
{
  "listInfo": {"limit": 20, "offset": 0, "count": 76},
  "items": [
    {
      "phoneNumber": "998900123456",
      "numberClass": {
        "numberClassId": 1,
        "name": "Обычный"
      },
      "phoneNumberCharge": {
        "amount": 0
      }
    },
    {
      "phoneNumber": "998901234567",
      "numberClass": {
        "numberClassId": 107,
        "name": "GOLD"
      },
      "phoneNumberCharge": {
        "amount": 500000
      }
    }
  ]
}
```

**Классы номеров:**
- `1` - Обычный (бесплатно)
- `107` - GOLD (платный)
- `108` - SILVER (платный)
- `111` - Steel (платный)

---

### 🔄 10. PSIX API (Legacy XML/HTTP)

#### **GET /PSIX/scli/UCELL_NEXT_TIME_FEE**
**Цель:** Альтернативный способ получения следующей абонплаты

**Параметры запроса:**
```
GET https://sbms.ucell/PSIX/scli/UCELL_NEXT_TIME_FEE
?SESSION_ID={TOKEN}
&P_SUBS_ID=1252275098
```

**Формат ответа:** JSON/XML (зависит от эндпоинта)

---

#### **GET /PSIX/scli/UCELL_DISCOUNTS_INFO**
**Цель:** Информация о скидках абонента (альтернатива rtDiscounts)

**Параметры запроса:**
```
GET https://sbms.ucell/PSIX/scli/UCELL_DISCOUNTS_INFO
?SESSION_ID={TOKEN}
&P_SUBS_ID=1252275098
&SUBSCRIBER_MSISDN=998500173054
```

---

## 🔄 ПРОЦЕССЫ ТЕСТИРОВАНИЯ

### 🧪 Процесс 1: БАЗОВАЯ ПРОВЕРКА (6 Core Tests)

```
┌───────────────────────────────────────────────────────────┐
│                   БАЗОВЫЙ ТЕСТОВЫЙ ЦИКЛ                    │
└───────────────────────────────────────────────────────────┘

1. 🔐 АВТОРИЗАЦИЯ
   └─> GET /OAPI/v1/tokens-stub/get
       └─> Получение SESSION_ID
           └─> Сохранение токена для последующих запросов

2. 👤 ПОИСК КЛИЕНТА
   └─> GET /OAPI/v1/customers/searchBase?identification={MSISDN}
       └─> Извлечение:
           ├─> customerId (для баланса, атрибутов)
           ├─> subscriberId (для тарифов, пакетов)
           ├─> ratePlanId (текущий тариф)
           └─> status (статус абонента)

3. 📊 ОСТАТКИ ПАКЕТОВ
   └─> GET /PSAPI/.../{subscriberId}/rtDiscounts
       └─> Агрегация по measureUnitId:
           ├─> 1 → минуты
           ├─> 7 → SMS
           └─> 14 → МБ

4. 📋 АТРИБУТЫ КЛИЕНТА
   └─> GET /OAPI/.../{customerId}/currentCustomAttributes
       └─> Проверка наличия паспортных данных, ПИН

5. 📝 СВОЙСТВА ОБРАЩЕНИЙ
   └─> POST /OAPI/v1/inquiries/slaveCustomProperties
       └─> Валидация полей формы обращения

6. 📱 СВОБОДНЫЕ НОМЕРА
   └─> GET /OAPI/.../phoneNumbers/free
       └─> Проверка доступности номеров для продажи
```

**Время выполнения:** ~5-10 секунд  
**Результат:** JSON отчет с детализацией по каждому тесту

---

### 🔄 Процесс 2: СМЕНА ТАРИФА (Tariff Change)

```
┌───────────────────────────────────────────────────────────┐
│            ПРОЦЕСС СМЕНЫ ТАРИФА С ВАЛИДАЦИЕЙ              │
└───────────────────────────────────────────────────────────┘

ЭТАП 1: ПОДГОТОВКА
├─> 1.1. Авторизация (получение токена)
├─> 1.2. Поиск клиента (получение customerId, subscriberId)
└─> 1.3. Баланс ДО смены
    └─> GET /PSAPI/.../availableBalance
        └─> Сохранение balanceBefore

ЭТАП 2: АНАЛИЗ ТЕКУЩЕГО СОСТОЯНИЯ
├─> 2.1. rtDiscounts ДО смены
│   └─> Извлечение списка productId (для diff после смены)
│       └─> pids_before = {258, 257, 0, 1465744712}
│
└─> 2.2. Поиск целевого тарифа
    ├─> Если targetRatePlanId задан → использовать
    └─> Иначе → GET /OAPI/.../availableForChange/search
        └─> Поиск по имени тарифа
            └─> target_rp_id = 78, target_rp_name = "Bor 60"

ЭТАП 3: ПРОВЕРКА СТОИМОСТИ
└─> 3.1. Получение АП целевого тарифа
    └─> GET /OAPI/.../nextCharges/ratePlans/{target_rp_id}
        └─> Извлечение recurringCharges[0].amount
            └─> target_fee = 60000 сум

ЭТАП 4: ВЫПОЛНЕНИЕ СМЕНЫ
└─> 4.1. Запрос смены тарифа
    └─> POST /OAPI/.../ratePlans/change?newRatePlanId=78
        └─> Ответ: {
              "ratePlanOrderId": "o10420443418",
              "status": {"name": "В процессе подключения"}
            }

ЭТАП 5: ОЖИДАНИЕ И ОПРОС СТАТУСА
└─> 5.1. Опрос статуса заказа (polling)
    ├─> for i in range(max_polls):  # 5-10 итераций
    │   ├─> sleep(poll_interval)    # 3-5 секунд
    │   ├─> GET /OAPI/.../ratePlans/orders
    │   └─> Поиск order по ratePlanOrderId
    │       └─> Проверка status.ratePlanOrderStatusId:
    │           ├─> 5 → "В процессе" (продолжаем ждать)
    │           ├─> 1 → "Подключен" ✅ (успех)
    │           └─> 7 → "Отклонён по балансу" ❌ (ошибка)
    │
    └─> 5.2. Fallback проверка через searchBase
        └─> GET /OAPI/.../searchBase
            └─> Проверка: ratePlan.ratePlanId == target_rp_id

ЭТАП 6: ВАЛИДАЦИЯ СМЕНЫ
└─> 6.1. Проверка тарифа
    └─> GET /OAPI/.../searchBase
        └─> Сравнение:
            ├─> new_rp_id == target_rp_id? ✅/❌
            └─> new_rp_name == target_rp_name? ✅/❌

ЭТАП 7: ОПРЕДЕЛЕНИЕ PRODUCT ID НОВОГО ТАРИФА
└─> 7.1. rtDiscounts ПОСЛЕ смены
    ├─> GET /PSAPI/.../rtDiscounts (с retry 3 раза, ждем 5 сек)
    │   └─> Извлечение списка productId
    │       └─> pids_after = {258, 257, 0, 1465744712, 259}
    │
    ├─> 7.2. Вычисление diff
    │   └─> new_pids = pids_after - pids_before
    │       └─> new_pids = {259}  # новый productId!
    │
    └─> 7.3. Выбор productId нового тарифа
        └─> Если несколько new_pids:
            └─> Выбрать productId с максимальным кол-вом items
        └─> Сохранение: target_product_id = 259

ЭТАП 8: ВАЛИДАЦИЯ ЛИМИТОВ НОВОГО ТАРИФА
└─> 8.1. Фильтрация rtDiscounts по productId
    └─> extract_volumes_by_product_id(rt_data, target_product_id)
        └─> Агрегация только items где productId == 259
            ├─> Минуты: expected=1000, actual=1000 ✅
            ├─> SMS: expected=1000, actual=1000 ✅
            └─> МБ: expected=10240, actual=10240 ✅

ЭТАП 9: ВАЛИДАЦИЯ СПИСАНИЯ С БАЛАНСА
├─> 9.1. Баланс ПОСЛЕ смены
│   └─> GET /PSAPI/.../availableBalance
│       └─> balanceAfter = 17603.33 сум
│
└─> 9.2. Расчет списания
    └─> charge = balanceBefore - balanceAfter
        ├─> expected_charge = 60000 сум
        ├─> actual_charge = 0 сум
        └─> Статус: ❌ FAIL (не списалось, т.к. заказ отклонен)

ЭТАП 10: ДОПОЛНИТЕЛЬНЫЕ ПРОВЕРКИ
├─> 10.1. След. абонплата
│   └─> GET /OAPI/.../nextCharges/ratePlans/{new_rp_id}
│       └─> Проверка amount == expectedNextFee
│
├─> 10.2. Доступные тарифы ПОСЛЕ
│   └─> GET /OAPI/.../availableForChange/search
│       └─> Проверка: count == expectedCountAfter
│
└─> 10.3. Доступные пакеты ПОСЛЕ
    └─> GET /PSAPI/.../packs/availableForActivate
        └─> Проверка: count == expectedPacksCountAfter

РЕЗУЛЬТАТ:
└─> TestReport {
      "checks": [
        {"name": "Баланс до смены", "status": "PASS"},
        {"name": "Целевой тариф", "status": "PASS"},
        {"name": "Результат смены", "status": "PASS"},
        {"name": "Статус заказа", "status": "FAIL", 
         "actual": "Отклонён по балансу"},
        {"name": "Тариф сменился", "status": "FAIL"},
        {"name": "Минуты", "status": "PASS"},
        {"name": "Списание", "status": "FAIL"}
      ],
      "summary": {"total": 11, "passed": 7, "failed": 3}
    }
```

**Время выполнения:** ~30-40 секунд  
**Критические моменты:**
1. ⚠️ Асинхронность смены тарифа (нужен polling)
2. ⚠️ Определение productId через diff (может быть несколько новых)
3. ⚠️ rtDiscounts обновляется с задержкой (нужны retry)

---

### 🔍 Процесс 3: ПРОВЕРКА ТАРИФА (Read-Only)

```
┌───────────────────────────────────────────────────────────┐
│            ВАЛИДАЦИЯ ТЕКУЩЕГО ТАРИФА                       │
└───────────────────────────────────────────────────────────┘

1. Поиск клиента
   └─> Получение: ratePlanName, ratePlanId

2. Проверка имени тарифа
   └─> compare_string(expected_name, actual_name)

3. RT Discounts → лимиты
   ├─> Минуты (measureUnitId=1)
   ├─> SMS (measureUnitId=7)
   └─> Интернет (measureUnitId=14)

4. Абонентская плата
   ├─> OAPI: nextCharges/ratePlans/{ratePlanId}
   └─> PSIX: UCELL_NEXT_TIME_FEE (альтернатива)

5. Активные пакеты
   └─> GET /OAPI/.../packs

6. Доступные пакеты
   └─> GET /PSAPI/.../packs/availableForActivate

7. Активные услуги
   └─> GET /OAPI/.../services

8. Доступные услуги
   └─> GET /PSAPI/.../services/availableForActivate

9. Доступные тарифы
   └─> GET /OAPI/.../availableForChange/search

10. Жизненный цикл
    └─> GET /PSAPI/.../licy-base-private/subslcstates/actual

11. Баланс
    └─> GET /PSAPI/.../availableBalance
```

---

## 📊 ВАЛИДАЦИЯ И ПРОВЕРКИ

### Типы проверок в CheckResult

```python
@dataclass
class CheckResult:
    name: str          # Название проверки
    category: str      # Категория: auth, search, volumes, fee, balance, etc.
    status: str        # PASS, FAIL, WARN, SKIP, ERROR
    expected: object   # Ожидаемое значение
    actual: object     # Фактическое значение
    message: str       # Дополнительное сообщение
    api_endpoint: str  # Какой API использовался
```

### Статусы проверок

- ✅ **PASS** - проверка прошла успешно
- ❌ **FAIL** - проверка провалена
- ⚠️ **WARN** - предупреждение (не критично)
- ⏭️ **SKIP** - проверка пропущена (не задано expected)
- 🔥 **ERROR** - критическая ошибка (exception)

### Функции сравнения

#### 1. compare_numeric (числа с допуском)
```python
def compare_numeric(expected, actual, name, unit="", tolerance_pct=1):
    """
    Сравнение числовых значений с толерантностью 1%
    
    expected=1000, actual=1005 → PASS (разница 0.5%)
    expected=1000, actual=1100 → FAIL (разница 10%)
    """
    diff = abs(actual - expected)
    tolerance = expected * tolerance_pct / 100
    
    if diff <= tolerance:
        return PASS
    else:
        return FAIL
```

**Примеры:**
- Минуты: 1000 ± 10 минут
- МБ: 10240 ± 102 МБ
- Абонплата: 60000 ± 600 сум

#### 2. compare_string (строки)
```python
def compare_string(expected, actual, name):
    """
    Сравнение строк (case-insensitive, partial match)
    
    expected="Bor 60", actual="Bor 60 Plus" → PASS
    expected="Bor", actual="Bor 60" → PASS
    """
    if expected.lower() in actual.lower():
        return PASS
    else:
        return FAIL
```

#### 3. compare_list_count (количество элементов)
```python
def compare_list_count(expected_count, actual_list, name):
    """
    Проверка количества элементов в списке
    
    expected=20, actual_list=[...20 items...] → PASS
    expected=20, actual_list=[...15 items...] → FAIL
    """
    if len(actual_list) == expected_count:
        return PASS
    else:
        return FAIL
```

#### 4. compare_list_names (наличие имен в списке)
```python
def compare_list_names(expected_names, actual_items, name_field, check_name):
    """
    Проверка что ожидаемые имена присутствуют в списке
    
    expected=["Pack 1", "Pack 2"]
    actual=[{name: "Pack 1"}, {name: "Pack 2"}, {name: "Pack 3"}]
    → PASS (все найдены)
    """
    missing = []
    for exp_name in expected_names:
        if not any(exp_name.lower() in item[name_field].lower() 
                   for item in actual_items):
            missing.append(exp_name)
    
    if not missing:
        return PASS
    else:
        return FAIL (missing: ...)
```

---

## 🗂️ ХРАНЕНИЕ РЕЗУЛЬТАТОВ

### Структура отчета TestReport

```python
{
  "testId": "20260218_144652_998500173054",  # timestamp + MSISDN
  "timestamp": "2026-02-18T14:46:52.013592",
  "duration": 38.74,                         # секунды
  "msisdn": "998500173054",
  "testType": "tariff_change",               # tariff, tariff_change, pack, service
  "targetName": "Bor 60",
  
  "customerInfo": {
    "customerId": 275296478,
    "subscriberId": 1252275098,
    "name": "RAIMOV XASAN SHERZODOVICH",
    "ratePlanName": "Yangi Start",
    "ratePlanId": 380,
    "status": "Действующий",
    "balanceBefore": 17603.33,
    "balanceAfter": 17603.33,
    "balance": 17603.33
  },
  
  "summary": {
    "total": 11,
    "passed": 7,
    "failed": 3,
    "warnings": 1,
    "skipped": 0,
    "errors": 0
  },
  
  "checks": [
    {
      "name": "Авторизация",
      "category": "auth",
      "status": "PASS",
      "message": "Token: AAAI3AAU...",
      "apiEndpoint": "/OAPI/v1/tokens-stub/get"
    },
    {
      "name": "Статус заказа",
      "category": "action",
      "status": "FAIL",
      "expected": "Выполнен",
      "actual": "Отклонён по балансу",
      "message": "Заказ o10420443418, опрошено 5 раз"
    }
  ],
  
  "rawResponses": {
    "searchBase": {...},              # полный JSON ответ
    "balanceBeforeChange": {...},
    "rtDiscountsBefore": {...},
    "targetNextChargesBefore": {...},
    "ratePlanChangeResult": {...},
    "orderPoll_1": {...},
    "orderPoll_2": {...},
    "searchBaseAfter": {...},
    "rtDiscountsAfter": {...},
    "balanceAfterChange": {...}
  }
}
```

### Сохранение в файл

```python
def save_report(report: TestReport, directory="test_history"):
    filename = f"{report.test_id}.json"  # 20260218_144652_998500173054.json
    filepath = os.path.join(directory, filename)
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
```

**Формат имени файла:** `YYYYMMDD_HHMMSS_MSISDN.json`

### История тестов

В проекте сохранено **36 исторических отчетов** в директории `test_history/`:
```
20260213_104917_998500173054.json
20260213_105020_998500173054.json
...
20260218_144652_998500173054.json
```

---

## 🌐 ВЕБ-ИНТЕРФЕЙСЫ

### 1. Dashboard (dashboard.html)

**URL:** `http://localhost:5000/`

**Функционал:**
- 40+ API эндпоинтов с интерактивными формами
- Группировка по категориям: core, customer, packs, services, rateplans, balances, etc.
- JSON viewer для ответов API
- Статистика тестов (Total/Pass/Fail/Time)
- История выполнения

**Категории эндпоинтов:**
```javascript
const endpoints = [
  // CORE (6)
  {id: 'get_session', name: '1. Get Session'},
  {id: 'customer_search', name: '2. Customer Search'},
  {id: 'rt_discounts', name: '3. RT Discounts'},
  {id: 'custom_attributes', name: '4. Custom Attributes'},
  {id: 'inquiry_props', name: '5. Inquiry Properties'},
  {id: 'free_numbers', name: '6. Free Numbers'},
  
  // CUSTOMER (3)
  {id: 'subscriber_info', name: 'Subscriber Info'},
  {id: 'customer_info', name: 'Customer Info'},
  {id: 'customer_header', name: 'Customer Header'},
  
  // PACKS (7)
  {id: 'active_packs', name: 'Active Packs'},
  {id: 'available_packs', name: 'Available Packs'},
  {id: 'activate_pack', name: 'Activate Pack'},
  {id: 'deactivate_pack', name: 'Deactivate Pack'},
  // ...
  
  // SERVICES (4)
  // RATE PLANS (8)
  // BALANCES (5)
  // SIMCARDS (1)
  // LIFECYCLE (2)
  // HAS_SBMS (5) - Legacy PSIX
  // PSIX (15) - Legacy procedures
  // OTHER (5)
]
```

### 2. QA Tester (tester.html)

**URL:** `http://localhost:5000/tester`

**Функционал:**
- Создание тестовых кейсов
- 4 типа тестов:
  - `tariff` - проверка тарифа
  - `tariff_change` - смена тарифа
  - `pack` - проверка пакета
  - `service` - проверка услуги
- JSON редактор для `expected` values
- Real-time progress bar
- Summary: Total/Pass/Fail/Warn/Time
- Customer info card
- Результаты в таблице
- Raw API responses (collapsible)
- История последних 50 тестов

**Пример формы:**
```html
<form>
  <input name="msisdn" value="998500173054">
  <select name="testType">
    <option value="tariff">Tariff (Read-Only)</option>
    <option value="tariff_change">Tariff Change</option>
    <option value="pack">Pack</option>
    <option value="service">Service</option>
  </select>
  <input name="targetName" value="Bor 60">
  <textarea name="expected">{
    "targetRatePlanId": 78,
    "minutesLimit": 1000,
    "smsLimit": 1000,
    "internetGb": 10
  }</textarea>
  <button>Run Test</button>
</form>
```

### 3. Tariff Test (tariff_test.html)

**URL:** `http://localhost:5000/tariff-test`

**Функционал:**
- Специализированный интерфейс для смены тарифов
- Шаг 1: Загрузка клиента
  - Ввод MSISDN
  - Автозагрузка: баланс, текущий тариф, объемы
- Шаг 2: Выбор тарифа из dropdown
  - Список всех доступных тарифов
  - Автозаполнение АП, productId
- Шаг 3: Настройка проверок
  - Автозаполнение ожидаемых лимитов
  - Редактирование parameters
- Шаг 4: Запуск теста
  - Real-time progress
  - Detailed results

**API endpoints:**
- `POST /api/tariff/load-customer` - загрузка данных клиента
- `POST /api/tariff/run` - запуск теста смены тарифа

---

## 🔧 REST API СЕРВЕРА

### Flask Server (server.py)

**Запуск:** `python server.py`  
**Порт:** 5000 (по умолчанию)

### Endpoints

#### 1. GET /
Главный дашборд (dashboard.html)

#### 2. GET /tester
QA Tester интерфейс (tester.html)

#### 3. GET /tariff-test
Tariff Test интерфейс (tariff_test.html)

#### 4. GET /api/config
Получение конфигурации

**Response:**
```json
{
  "base_url": "https://sbms.ucell",
  "timeout": 30,
  "login": "DBS_CC_OPERATORS_PSO",
  "password": "Ucell2026$$",
  "msisdn": "998500173054"
}
```

#### 5. POST /api/test/run
Запуск теста

**Request:**
```json
{
  "msisdn": "998500173054",
  "testType": "tariff_change",
  "targetName": "Bor 60",
  "login": "...",     // optional
  "password": "...",  // optional
  "expected": {
    "targetRatePlanId": 78,
    "minutesLimit": 1000,
    "smsLimit": 1000,
    "internetGb": 10,
    "expectedBalanceCharge": 60000,
    "expectedNextFee": 60000,
    "waitTimeSeconds": 5
  }
}
```

**Response:** JSON (полный TestReport)

#### 6. GET /api/test/history
Список последних 50 тестов

**Response:**
```json
[
  {
    "id": "20260218_144652_998500173054",
    "timestamp": "2026-02-18T14:46:52",
    "msisdn": "998500173054",
    "testType": "tariff_change",
    "targetName": "Bor 60",
    "summary": {"total": 11, "passed": 7, "failed": 3}
  }
]
```

#### 7. GET /api/test/history/{report_id}
Получение конкретного отчета

**Response:** JSON (полный TestReport)

#### 8. POST /api/tariff/load-customer
Загрузка данных клиента для Tariff Test

**Request:**
```json
{
  "msisdn": "998500173054",
  "login": "...",
  "password": "..."
}
```

**Response:**
```json
{
  "customer": {
    "customerId": 275296478,
    "subscriberId": 1252275098,
    "name": "RAIMOV XASAN SHERZODOVICH",
    "ratePlanName": "Yangi Start",
    "ratePlanId": 380,
    "status": "Действующий",
    "balance": 17603.33,
    "currentFee": 60000,
    "currentProductId": 258,
    "volumes": {"minutes": 1500, "sms": 45500, "mb": 61441},
    "volumesByTariff": {"minutes": 500, "sms": 500, "mb": 15360}
  },
  "availableRatePlans": [
    {"ratePlanId": 78, "name": "Bor 60"},
    {"ratePlanId": 79, "name": "Bor 80"}
  ],
  "activePacks": [...],
  "availablePacks": [...],
  "activeServices": [...],
  "availableServices": [...]
}
```

#### 9. POST /api/tariff/run
Запуск теста смены тарифа (алиас для POST /api/test/run с testType='tariff_change')

#### 10. GET/POST/PUT/DELETE /proxy/{path}
CORS Proxy для прямых запросов к SBMS API из браузера

**Example:**
```javascript
fetch('http://localhost:5000/proxy/OAPI/v1/customers/searchBase?identification=998500173054&authToken=...')
```

---

## 🎯 ИСПОЛЬЗОВАНИЕ ПРОЕКТА

### 1. Установка зависимостей

```bash
cd /Users/Macbook/Desktop/sbms_test

# Option 1: pip
pip install -r requirements.txt

# Option 2: pipenv
pipenv install
```

### 2. Конфигурация (.env)

```bash
# Уже настроено по умолчанию
SBMS_BASE_URL=https://sbms.ucell
SBMS_LOGIN=DBS_CC_OPERATORS_PSO
SBMS_PASSWORD=Ucell2026$$
TEST_MSISDN=998500173054
REQUEST_TIMEOUT=30
```

### 3. Запуск CLI тестов

```bash
# 6 core тестов
python run_tests.py

# Вывод:
# =================================================================
#   ТЕСТ 1: GET SESSION (Авторизация)
# =================================================================
#   PASS  Авторизация
#         Token: AAAI0gLupfeOAE692QI...
# 
#   PASS  Поиск клиента
#         Найден: RAIMOV XASAN SHERZODOVICH
# ...
```

### 4. Запуск веб-сервера

```bash
python server.py

# Вывод:
# =======================================================
#   UCELL SBMS API - Proxy Server
# =======================================================
#   Dashboard:    http://localhost:5000
#   QA Tester:    http://localhost:5000/tester
#   Tariff Test:  http://localhost:5000/tariff-test
#   Proxy:        http://localhost:5000/proxy/...
#   Target:       https://sbms.ucell
#   Timeout:      30s
# =======================================================
```

### 5. Открыть браузер

```bash
# macOS
open http://localhost:5000

# Windows
start http://localhost:5000

# Linux
xdg-open http://localhost:5000
```

### 6. Использование Python API

```python
from sbms_runner import TestRunner

# Инициализация
runner = TestRunner(
    base_url="https://sbms.ucell",
    login="DBS_CC_OPERATORS_PSO",
    password="Ucell2026$$",
    timeout=30
)

# Тест смены тарифа
test_case = {
    "msisdn": "998500173054",
    "testType": "tariff_change",
    "targetName": "Bor 60",
    "expected": {
        "targetRatePlanId": 78,
        "minutesLimit": 1000,
        "smsLimit": 1000,
        "internetGb": 10,
        "expectedBalanceCharge": 60000,
        "expectedNextFee": 60000
    }
}

report = runner.run(test_case)

print(f"Duration: {report.duration:.2f}s")
print(f"Summary: {report.summary}")
print(f"Checks: {len(report.checks)}")

for check in report.checks:
    status_icon = "✅" if check.status == "PASS" else "❌"
    print(f"{status_icon} {check.name}: {check.status}")
```

---

## 📈 ТИПИЧНЫЕ СЦЕНАРИИ ИСПОЛЬЗОВАНИЯ

### Сценарий 1: Smoke Testing (Быстрая проверка)

**Цель:** Проверить что API доступен и основные эндпоинты работают

```bash
python run_tests.py
```

**Результат:** 6 тестов за ~10 секунд

---

### Сценарий 2: Регрессионное тестирование после обновления

**Цель:** Проверить что изменения в SBMS не сломали существующую функциональность

```bash
# Запустить full suite через dashboard
open http://localhost:5000
# -> Run Core Tests (1-6)
# -> Run Category: Packs
# -> Run Category: Services
# -> Run Category: Rate Plans
```

**Результат:** Детальный отчет по всем категориям API

---

### Сценарий 3: Тестирование новой версии тарифа

**Цель:** Проверить что новый тариф корректно настроен (лимиты, АП, пакеты)

```bash
open http://localhost:5000/tester

# Заполнить форму:
# - MSISDN: 998500173054
# - Test Type: tariff
# - Target Name: "Новый Тариф v2"
# - Expected:
#   {
#     "minutesLimit": 2000,
#     "smsLimit": 2000,
#     "internetGb": 20,
#     "monthlyFee": 80000,
#     "availablePacksCount": 25
#   }

# -> Run Test
```

**Результат:** Валидация всех параметров тарифа

---

### Сценарий 4: Автоматизация смены тарифа в Production

**Цель:** Массовая смена тарифов для списка абонентов

```python
# mass_tariff_change.py
import csv
from sbms_runner import TestRunner

runner = TestRunner("https://sbms.ucell", "...", "...")

with open("subscribers.csv") as f:
    reader = csv.DictReader(f)
    for row in reader:
        test_case = {
            "msisdn": row["msisdn"],
            "testType": "tariff_change",
            "targetName": row["new_tariff"],
            "expected": {"targetRatePlanId": int(row["tariff_id"])}
        }
        
        report = runner.run(test_case)
        
        if report.summary["failed"] > 0:
            print(f"FAILED: {row['msisdn']} -> {row['new_tariff']}")
            for check in report.checks:
                if check.status == "FAIL":
                    print(f"  - {check.name}: {check.message}")
        else:
            print(f"SUCCESS: {row['msisdn']} -> {row['new_tariff']}")
```

---

### Сценарий 5: Мониторинг API (Cron Job)

**Цель:** Периодическая проверка доступности и работоспособности API

```bash
# /etc/cron.d/sbms-monitor
*/15 * * * * cd /path/to/sbms_test && python run_tests.py > /var/log/sbms_tests.log 2>&1
```

**Alert:** Если failed > 0 → отправить уведомление

---

## ⚠️ ИЗВЕСТНЫЕ ПРОБЛЕМЫ И ОГРАНИЧЕНИЯ

### 1. Inquiry Properties API возвращает HTTP 400

**Проблема:**
```json
{
  "name": "Свойства обращений",
  "status": "FAIL",
  "details": "HTTP 400"
}
```

**Причины:**
- Некорректное тело запроса (изменился формат API)
- Недостаточные права у пользователя `DBS_CC_OPERATORS_PSO`
- Некорректный `topicId` или `customPropertyDeclarationId`

**Решение:**
- Проверить актуальность Postman коллекции
- Проверить права доступа пользователя
- Использовать другой топик обращения

---

### 2. subscriptionId ≠ productId

**Проблема:** После смены тарифа невозможно найти productId для фильтрации rtDiscounts

**Объяснение:**
- `subscriptionId` из `nextCharges` - это внутренний ID подписки
- `productId` из `rtDiscounts` - это ID продукта в системе скидок
- Они **не совпадают**

**Решение в проекте:**
```python
# Определение productId через diff ДО/ПОСЛЕ смены
pids_before = {258, 257, 0}
pids_after = {258, 257, 0, 259}
new_pid = pids_after - pids_before  # {259}
target_product_id = 259
```

---

### 3. rtDiscounts обновляется с задержкой

**Проблема:** Сразу после смены тарифа в rtDiscounts еще нет новых items

**Решение:**
```python
# Retry с задержкой
for attempt in range(3):
    if attempt > 0:
        time.sleep(5)
    rt_data = client.get_rt_discounts(subscriber_id)
    # проверка наличия нового productId
```

---

### 4. Смена тарифа отклоняется по балансу

**Проблема:**
```json
{
  "status": {"name": "Отклонён по балансу"}
}
```

**Причина:** Недостаточно средств на балансе для оплаты АП нового тарифа

**Решение:**
- Пополнить баланс перед тестом
- Использовать тестовый MSISDN с достаточным балансом
- Проверять баланс перед сменой: `balance >= expectedFee`

---

## 📊 СТАТИСТИКА ПРОЕКТА

### Размеры файлов
- `sbms_client.py`: 262 строки
- `sbms_runner.py`: 719 строк
- `sbms_checks.py`: 366 строк
- `server.py`: 311 строк
- `run_tests.py`: 551 строка
- `dashboard.html`: 1577 строк
- `tester.html`: 864 строки
- `tariff_test.html`: ~800 строк (по аналогии)

**Итого:** ~5500 строк кода

### API Coverage
- **40+ эндпоинтов** в dashboard
- **6 core тестов** в run_tests.py
- **4 типа тестов** в test runner
- **3 веб-интерфейса**

### История тестов
- **36 исторических отчета** в test_history/
- Период: 13.02.2026 - 18.02.2026
- Средняя длительность теста: 30-40 секунд

---

## 🎓 ВЫВОДЫ И РЕКОМЕНДАЦИИ

### ✅ Сильные стороны проекта

1. **Модульная архитектура**
   - Четкое разделение: client → runner → checks
   - Легко добавлять новые тесты

2. **Множественные интерфейсы**
   - CLI для автоматизации
   - Web dashboard для ручного тестирования
   - REST API для интеграций

3. **Подробные отчеты**
   - Сохранение всех API ответов
   - Детальные проверки с категориями
   - История тестов

4. **Умная валидация**
   - Фильтрация rtDiscounts по productId
   - Определение productId через diff
   - Множественные источники данных (OAPI + PSIX)

5. **CORS Proxy**
   - Работа с SBMS API из браузера

---

### ⚠️ Области для улучшения

1. **Тесты**
   - Отсутствует директория `tests/` с unit-тестами
   - pytest настроен но не используется

2. **Логирование**
   - Нет централизованного logging
   - Только print() в консоль

3. **Async**
   - Все запросы синхронные
   - Можно ускорить через asyncio/aiohttp

4. **Type Hints**
   - Частичное использование
   - Стоит добавить везде для лучшей поддержки IDE

5. **Error Handling**
   - Некоторые try/except слишком широкие
   - Стоит добавить специфичные exception классы

6. **Документация**
   - API endpoints не покрыты docstrings
   - Нет Swagger/OpenAPI спецификации

---

## 🎯 ЗАКЛЮЧЕНИЕ

**UCELL SBMS API Test Framework** - это **профессиональный, полнофункциональный инструмент** для автоматизированного тестирования биллинговой системы SBMS.

### Ключевые достижения:
✅ **40+ API эндпоинтов** покрыто тестами  
✅ **6 core тестов** для smoke testing  
✅ **Автоматическая смена тарифов** с full validation  
✅ **3 веб-интерфейса** для разных use cases  
✅ **История тестов** с детальными отчетами  
✅ **CORS Proxy** для работы из браузера  

### Применение:
- ✅ QA Testing
- ✅ Smoke Tests
- ✅ Regression Testing
- ✅ Tariff Migration
- ✅ API Monitoring
- ✅ Debugging
- ✅ Documentation

### Готовность:
**🟢 Production Ready** - проект готов к использованию в реальных условиях  
**🟡 Needs Minor Improvements** - небольшие доработки повысят качество

---

**Версия анализа:** 1.0  
**Дата:** 20 февраля 2026 г.  
**Автор:** AI System Analysis
