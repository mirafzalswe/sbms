# 🔄 Тест смены тарифа - Руководство

## 📋 Описание

Новый тип теста `tariff_change` автоматически:
1. Получает баланс ДО смены
2. Получает список доступных тарифов
3. Находит целевой тариф
4. Выполняет смену тарифа
5. Ждет применения изменений (5 сек по умолчанию)
6. Проверяет что тариф изменился (по productId)
7. Проверяет лимиты нового тарифа (минуты, SMS, интернет)
8. Проверяет списание с баланса
9. Проверяет следующую абонплату
10. Проверяет доступные тарифы после смены
11. Проверяет доступные пакеты после смены

## 🚀 Использование через API

### Пример запроса:

```json
POST /api/test/run

{
  "msisdn": "998500173054",
  "testType": "tariff_change",
  "targetName": "Barcha 65",
  "expected": {
    "targetRatePlanId": 123,
    "targetRatePlanName": "Barcha 65",
    "targetProductId": 258,
    "minutesLimit": 1000,
    "smsLimit": 1000,
    "internetGb": 10,
    "expectedBalanceCharge": 65000,
    "expectedNextFee": 65000,
    "waitTimeSeconds": 5,
    "availableRatePlansCount": 15,
    "availableRatePlansCountAfter": 14,
    "availablePacksCountAfter": 20,
    "availablePackNamesAfter": ["Пакет 1", "Пакет 2"]
  }
}
```

### Параметры expected:

| Параметр | Тип | Описание |
|----------|-----|----------|
| `targetRatePlanId` | number | ID целевого тарифа (обязательно если нет targetRatePlanName) |
| `targetRatePlanName` | string | Название целевого тарифа (обязательно если нет targetRatePlanId) |
| `targetProductId` | number | Product ID для проверки в rtDiscounts |
| `minutesLimit` | number | Ожидаемые минуты после смены |
| `smsLimit` | number | Ожидаемые SMS после смены |
| `internetGb` | number | Ожидаемый интернет в ГБ |
| `internetMb` | number | Ожидаемый интернет в МБ (альтернатива internetGb) |
| `expectedBalanceCharge` | number | Ожидаемое списание с баланса |
| `expectedNextFee` | number | Ожидаемая следующая АП |
| `waitTimeSeconds` | number | Время ожидания после смены (по умолчанию 5) |
| `availableRatePlansCount` | number | Кол-во доступных тарифов ДО смены |
| `availableRatePlansCountAfter` | number | Кол-во доступных тарифов ПОСЛЕ смены |
| `availableRatePlanNamesAfter` | array | Названия доступных тарифов ПОСЛЕ |
| `availablePacksCountAfter` | number | Кол-во доступных пакетов ПОСЛЕ |
| `availablePackNamesAfter` | array | Названия доступных пакетов ПОСЛЕ |

## 📊 Пример ответа:

```json
{
  "testId": "20260216_120000_998500173054",
  "timestamp": "2026-02-16T12:00:00",
  "duration": 8.5,
  "msisdn": "998500173054",
  "testType": "tariff_change",
  "targetName": "Barcha 65",
  "customerInfo": {
    "customerId": 275296478,
    "subscriberId": 1252275098,
    "name": "RAIMOV XASAN SHERZODOVICH",
    "ratePlanName": "Barcha 65",
    "ratePlanId": 123,
    "balance": 435000
  },
  "summary": {
    "total": 18,
    "passed": 17,
    "failed": 1,
    "warnings": 0,
    "skipped": 0,
    "errors": 0
  },
  "checks": [
    {
      "name": "Баланс до смены",
      "category": "balance",
      "status": "PASS",
      "actual": "500000 сум"
    },
    {
      "name": "Поиск целевого тарифа",
      "category": "rateplan",
      "status": "PASS",
      "expected": "Barcha 65",
      "actual": "ID: 123, Название: Barcha 65"
    },
    {
      "name": "Смена тарифа",
      "category": "action",
      "status": "PASS",
      "message": "Начинаем смену на Barcha 65 (ID: 123)"
    },
    {
      "name": "Результат смены тарифа",
      "category": "action",
      "status": "PASS",
      "message": "Тариф успешно изменен"
    },
    {
      "name": "Проверка смены тарифа (productId)",
      "category": "rateplan",
      "status": "PASS",
      "expected": "ID: 123",
      "actual": "ID: 123, Название: Barcha 65"
    },
    {
      "name": "Проверка productId в rtDiscounts",
      "category": "volumes",
      "status": "PASS",
      "expected": "productId: 258",
      "actual": "Найден"
    },
    {
      "name": "Минуты (после смены)",
      "category": "volumes",
      "status": "PASS",
      "expected": "1000 мин",
      "actual": "1000 мин"
    },
    {
      "name": "Списание с баланса",
      "category": "balance",
      "status": "PASS",
      "expected": "65000 сум",
      "actual": "65000 сум",
      "message": "До: 500000 сум, После: 435000 сум, Разница: 65000 сум"
    }
  ],
  "rawResponses": {
    "balanceBeforeChange": {...},
    "availableRatePlansBeforeChange": {...},
    "targetRatePlanNextCharges": {...},
    "ratePlanChangeResult": {...},
    "searchBaseAfterChange": {...},
    "rtDiscountsAfterChange": {...},
    "balanceAfterChange": {...},
    "ratePlanNextChargesAfter": {...},
    "availableRatePlansAfterChange": {...},
    "availablePacksAfterChange": {...}
  }
}
```

## ✅ Что проверяется:

### 1. До смены:
- ✅ Баланс
- ✅ Количество доступных тарифов
- ✅ Наличие целевого тарифа
- ✅ Стоимость нового тарифа (след. АП)

### 2. Процесс смены:
- ✅ Успешность API вызова смены тарифа
- ✅ Ожидание применения изменений

### 3. После смены:
- ✅ Тариф изменился (проверка по ratePlanId)
- ✅ Product ID в rtDiscounts соответствует ожидаемому
- ✅ Лимиты (минуты, SMS, интернет)
- ✅ Списание с баланса
- ✅ Следующая абонплата
- ✅ Количество доступных тарифов
- ✅ Названия доступных тарифов
- ✅ Количество доступных пакетов
- ✅ Названия доступных пакетов

## 🔧 Запуск через Python:

```python
from test_engine import TestRunner

runner = TestRunner(
    base_url="https://sbms.ucell",
    login="DBS_CC_OPERATORS_PSO",
    password="Ucell2026$"
)

test_case = {
    "msisdn": "998500173054",
    "testType": "tariff_change",
    "targetName": "Barcha 65",
    "expected": {
        "targetRatePlanId": 123,
        "minutesLimit": 1000,
        "smsLimit": 1000,
        "internetGb": 10,
        "expectedBalanceCharge": 65000
    }
}

report = runner.run(test_case)
print(f"Результат: {report.summary}")
```

## 📝 Примечания:

1. **Время ожидания**: По умолчанию 5 секунд. Можно изменить через `waitTimeSeconds`
2. **Поиск тарифа**: Можно указать либо `targetRatePlanId`, либо `targetRatePlanName`
3. **Списание**: Если не указано `expectedBalanceCharge`, используется АП тарифа
4. **Product ID**: Опциональная проверка для сверки лимитов по конкретному продукту

## 🎯 Сценарии использования:

### Сценарий 1: Базовая смена тарифа
```json
{
  "testType": "tariff_change",
  "expected": {
    "targetRatePlanName": "Barcha 65",
    "minutesLimit": 1000,
    "smsLimit": 1000,
    "internetGb": 10
  }
}
```

### Сценарий 2: Полная валидация
```json
{
  "testType": "tariff_change",
  "expected": {
    "targetRatePlanId": 123,
    "targetProductId": 258,
    "minutesLimit": 1000,
    "smsLimit": 1000,
    "internetGb": 10,
    "expectedBalanceCharge": 65000,
    "expectedNextFee": 65000,
    "availableRatePlansCountAfter": 14,
    "availablePacksCountAfter": 20
  }
}
```

### Сценарий 3: Проверка доступности после смены
```json
{
  "testType": "tariff_change",
  "expected": {
    "targetRatePlanName": "Barcha 65",
    "availableRatePlanNamesAfter": ["Barcha 95", "Barcha 125"],
    "availablePackNamesAfter": ["Пакет Интернет 5ГБ", "Пакет Минуты 500"]
  }
}
```
