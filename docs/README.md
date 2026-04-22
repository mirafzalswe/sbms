# UCELL SBMS API - Test Framework

Автоматизированное тестирование SBMS API.

## Структура проекта

```
sbms_test/
├── .env                                  # Конфигурация (логин, пароль, MSISDN)
├── run_tests.py                          # Основной скрипт тестирования (6 тестов)
├── dashboard.html                        # Интерактивный веб-дашборд
├── TESTING_REPORT.md                     # Подробный отчет по API
├── UCELL_API.postman_collection.json     # Postman коллекция (справочник)
├── requirements.txt                      # Python зависимости
└── test_results.json                     # Результаты последнего запуска (auto)
```

## Быстрый старт

```bash
# 1. Установка зависимостей
pip install requests python-dotenv urllib3

# 2. Настройка (при необходимости отредактировать .env)
# Логин/пароль/MSISDN уже заданы по умолчанию

# 3. Запуск тестов
python run_tests.py

# 4. Открыть дашборд в браузере
open dashboard.html
```

## 6 основных тестов

| # | Тест | Метод | Endpoint | Что проверяет |
|---|------|-------|----------|---------------|
| 1 | Get Session | GET | `/OAPI/v1/tokens-stub/get` | Авторизация, получение токена |
| 2 | Customer Search | GET | `/OAPI/v1/customers/searchBase` | Поиск клиента по MSISDN |
| 3 | RT Discounts | GET | `/PSAPI/v1/bis-brt-balance/subscribers/{id}/rtDiscounts` | Остатки пакетов (мин, SMS, МБ) |
| 4 | Custom Attributes | GET | `/OAPI/v1/customers/{id}/currentCustomAttributes` | Атрибуты клиента (паспорт, ПИН) |
| 5 | Inquiry Properties | POST | `/OAPI/v1/inquiries/slaveCustomProperties` | Свойства обращений |
| 6 | Free Numbers | GET | `/OAPI/v1/networkResources/phoneNumbers/free` | Свободные номера |

## Порядок выполнения

```
Тест 1 (токен) -> Тест 2 (поиск) -> Тесты 3-6 (параллельно)
                       |
                       +-> customerId, subscriberId используются в тестах 3-4
```

## Конфигурация (.env)

```env
SBMS_BASE_URL=https://sbms.ucell
SBMS_LOGIN=DBS_CC_OPERATORS_PSO
SBMS_PASSWORD=Ucell2026$$
TEST_MSISDN=998500173054
REQUEST_TIMEOUT=30
```

## Результаты

После запуска `run_tests.py` создается файл `test_results.json` с результатами:

```json
{
  "timestamp": "2026-02-11T10:30:00",
  "summary": {
    "total": 6,
    "passed": 6,
    "failed": 0,
    "success_rate": "100%"
  },
  "tests": [...]
}
```

## Авторизация

SBMS использует XML-based токены:

```
GET /OAPI/v1/tokens-stub/get?login=XXX&password=YYY

Response (XML):
<SELFCARE>
  <SESSION_ID>AAAI0gHdY84H1leq...</SESSION_ID>
</SELFCARE>
```

Токен передается в остальных запросах как `?authToken=SESSION_ID`.

## ✨ Новинка: Названия скидок из Excel (Feb 2026)

**Автоматическое отображение корректных названий скидок** в блоке "Лимиты текущего тарифа".

- ✅ Загрузка 2041+ маппингов `discountPlanId → описание` из `DISPID.xlsx`
- ✅ Интеграция с API через `discount_mapper.py` → `sbms_checks.py`
- ✅ UI обновлен: tariff_test.html показывает названия + ID
- ✅ Fallback механизм: Excel → API → прочерк

**Было:** `Интернет | — | 10 ГБ`  
**Стало:** `Интернет | Общий интернет в рамках ТП (ID: 101) | 10 ГБ`

📖 Подробности: [DISCOUNT_MAPPER_INTEGRATION.md](DISCOUNT_MAPPER_INTEGRATION.md)

---

**Дата обновления:** 20 февраля 2026 г.
