# UCELL SBMS API - Отчет по тестированию

## Общая информация

| Параметр | Значение |
|----------|----------|
| **Система** | UCELL SBMS (Subscriber Billing Management System) |
| **Base URL** | `https://sbms.ucell` |
| **Авторизация** | Token-based (XML ответ с SESSION_ID) |
| **Формат ответов** | JSON (OAPI/PSAPI), XML (PSIX/tokens) |
| **Протокол** | HTTPS (SSL verify отключен для внутренней сети) |

---

## Группы API (из Postman Collection)

### 1. SESSION (Авторизация)
| Запрос | Метод | Путь | Описание |
|--------|-------|------|----------|
| get_session | GET | `/OAPI/v1/tokens-stub/get` | Получение токена сессии |

**Что тестировать:** Авторизация возвращает SESSION_ID в XML. Токен используется во всех последующих запросах.

---

### 2. CUSTOMER SEARCH (Поиск клиентов)
| Запрос | Метод | Путь | Описание |
|--------|-------|------|----------|
| searchBase | GET | `/OAPI/v1/customers/searchBase` | Поиск клиента по номеру телефона или аккаунту |
| customers | GET | `/OAPI/v1/customers/{id}` | Детальная информация о клиенте |
| customerHeaderInfo | GET | `/OAPI/v1/sbms/customerHeaderInfo` | Краткая информация о клиенте |
| subscribers | GET | `/OAPI/v1/subscribers/{id}` | Информация об абоненте |

**Что тестировать:**
- Поиск по `identification` (номер телефона, например `998500173054`)
- Поиск по `accountNumber` (номер лицевого счета)
- Проверка что в ответе есть `customerId`, `subscriberId`, `customer.name`, `ratePlan`

**Что ожидать в ответе:**
- `searchResults[].customerId` - ID клиента
- `searchResults[].subscriberId` - ID абонента
- `searchResults[].customer.name` - ФИО
- `searchResults[].firstSubscriber.identification` - MSISDN
- `searchResults[].firstSubscriber.ratePlan.name` - Текущий тариф

---

### 3. BALANCES (Баланс и скидки)
| Запрос | Метод | Путь | Описание |
|--------|-------|------|----------|
| rtDiscounts | GET | `/PSAPI/v1/bis-brt-balance/subscribers/{id}/rtDiscounts` | Пакетные остатки абонента |
| availableBalance | GET | `/PSAPI/v1/bis-base/customers/{id}/availableBalance` | Доступный баланс |
| rtBalance | GET | `/PSAPI/v1/bis-brt-balance/customers/{id}/rtBalance` | RT баланс клиента |
| balances/events/days | GET/POST | `/OAPI/v1/sbms/customers/{id}/balances/events/days` | История баланса по дням |

**Что тестировать:**
- `rtDiscounts` - основной запрос для остатков пакетов
- Параметр `customerDatabaseId=902` обязателен
- `callCreditIds` - опционально, для фильтрации конкретных скидок

**Что ожидать в ответе rtDiscounts:**
- `items[].measureUnitId` - единица измерения (1=минуты, 7=SMS, 14=МБ)
- `items[].spentVolume` - использовано
- `items[].maxVolume` - максимум
- `items[].startDate` / `items[].endDate` - период действия
- `items[].discountType` - тип скидки (4=пакетная, 6=безлимит, 11=дневная, 12=бонусная)

**Расшифровка measureUnitId:**
| ID | Единица |
|----|---------|
| 0 | Без единицы (денежный) |
| 1 | Минуты |
| 7 | SMS |
| 14 | МБ (мегабайты) |

---

### 4. CUSTOM ATTRIBUTES (Атрибуты клиента)
| Запрос | Метод | Путь | Описание |
|--------|-------|------|----------|
| currentCustomAttributes | GET | `/OAPI/v1/customers/{id}/currentCustomAttributes` | Пользовательские атрибуты клиента |

**Что тестировать:**
- Получение паспортных данных (customAttributeId=20 - дата окончания паспорта)
- ПИН ФЛ (customAttributeId=33)
- Признак подключения через МП (customAttributeId=553)

**Что ожидать в ответе:**
- `items[].customAttributeId` - ID атрибута
- `items[].name` - Название атрибута
- `items[].dataType` - Тип данных (STRING, DATE, DICTIONARY)
- `items[].values[].value` - Значение

---

### 5. INQUIRIES (Обращения)
| Запрос | Метод | Путь | Описание |
|--------|-------|------|----------|
| slaveCustomProperties | POST | `/OAPI/v1/inquiries/slaveCustomProperties` | Зависимые свойства обращений |
| inquiries | POST | `/OAPI/v1/inquiries` | Создание обращения |

**Что тестировать:**
- Получение зависимых свойств по `masterCustomPropertyDeclarationId=1045` (Тип обращения)
- Проверка доступности полей (accessibility: MANDATORY, HIDDEN)

**Что ожидать в ответе:**
- `customPropertyValues[]` - значения свойств
- `customPropertyAccessibilities[]` - доступность полей
- Поля: Тип обращения, Категория обращения, Причина обращения

---

### 6. PHONE NUMBERS (Свободные номера)
| Запрос | Метод | Путь | Описание |
|--------|-------|------|----------|
| phoneNumbers/free | GET | `/OAPI/v1/networkResources/phoneNumbers/free` | Свободные номера телефонов |

**Что тестировать:**
- Параметры: `standardId=1` (GSM), `freeMonthPeriod=2`
- Класс номера: Обычный (1), GOLD (107), SILVER (108), Steel (111)

**Что ожидать в ответе:**
- `listInfo.count` - общее количество свободных номеров
- `items[].phoneNumber` - номер телефона
- `items[].numberClass.name` - класс номера
- `items[].phoneNumberCharge.amount` - стоимость номера

---

### 7. PACKS (Пакеты) - дополнительно
| Запрос | Метод | Путь | Описание |
|--------|-------|------|----------|
| packs/search | GET | `/OAPI/v1/subscribers/{id}/packs` | Текущие пакеты абонента |
| availableForActivate | GET | `/PSAPI/v1/bis-base/subscribers/{id}/packs/availableForActivate` | Доступные для активации |
| packs/activate | POST | `/OAPI/v1/subscribers/{id}/packs/activate` | Активация пакета |
| packs/deactivate | POST | `/OAPI/v1/subscribers/{id}/packs/{packInstanceId}/deactivate` | Деактивация пакета |

### 8. SERVICES (Услуги) - дополнительно
| Запрос | Метод | Путь | Описание |
|--------|-------|------|----------|
| services | GET | `/OAPI/v1/subscribers/{id}/services` | Услуги абонента |
| services/activate | POST | `/OAPI/v1/subscribers/{id}/services/activate` | Активация услуги |
| availableForActivate | GET | `/PSAPI/v1/bis-base/subscribers/{id}/services/availableForActivate` | Доступные услуги |

### 9. RATE PLANS (Тарифы) - дополнительно
| Запрос | Метод | Путь | Описание |
|--------|-------|------|----------|
| ratePlans/change | POST | `/OAPI/v1/subscribers/{id}/ratePlans/change` | Смена тарифа |
| ratePlans/orders | GET | `/OAPI/v1/subscribers/{id}/ratePlans/orders` | Заказы на смену |
| availableForChange | POST | `/OAPI/v1/cbss/subscribers/{id}/ratePlans/availableForChange/search` | Доступные тарифы |

### 10. SIM CARDS - дополнительно
| Запрос | Метод | Путь | Описание |
|--------|-------|------|----------|
| SIMCards/search | POST | `/PSAPI/v1/bis-base/subscribers/{id}/SIMCards/search` | Поиск SIM-карт |
| SIMCards/history | GET | `/OAPI/v1/oapi-bis-service/SIMCards/{id}/history` | История SIM |

### 11. LIFE CYCLE - дополнительно
| Запрос | Метод | Путь | Описание |
|--------|-------|------|----------|
| actual | POST | `/PSAPI/internal/v1/licy-base-private/subslcstates/actual/{id}` | Текущий LC статус |
| history | POST | `/PSAPI/internal/v1/licy-base-private/subslcstates/history/search` | История LC |

### 12. PAYMENTS (Платежи) - дополнительно
| Запрос | Метод | Путь | Описание |
|--------|-------|------|----------|
| payments | GET | `/PSAPI/v1/fim/customers/{id}/payments` | История платежей |

### 13. REPORTS (Отчеты) - дополнительно
| Запрос | Метод | Путь | Описание |
|--------|-------|------|----------|
| CDM_CLIENT_REP_REQUEST | GET | `/CDM_REPORTS/serv/CDM_CLIENT_REP_REQUEST` | Генерация отчетов (PDF) |

---

## Порядок тестирования (Flow)

```
1. GET SESSION (получить токен)
       |
       v
2. CUSTOMER SEARCH (найти клиента по MSISDN)
       |
       v  получаем customerId + subscriberId
       |
   +---+---+---+---+
   |   |   |   |   |
   v   v   v   v   v
3. BALANCE  4. ATTRIBUTES  5. INQUIRIES  6. FREE NUMBERS  7. PACKS/SERVICES
```

## Как запускать тесты

```bash
# Установка зависимостей
pip install requests python-dotenv urllib3

# Быстрый тест (все 6 основных сценариев)
python run_tests.py

# Открыть интерактивный дашборд
open dashboard.html
```

## Коды ответов

| Код | Значение |
|-----|----------|
| 200 | Успех |
| 400 | Неверные параметры |
| 401 | Не авторизован (токен истек) |
| 403 | Нет прав доступа |
| 404 | Ресурс не найден |
| 500 | Ошибка сервера |
