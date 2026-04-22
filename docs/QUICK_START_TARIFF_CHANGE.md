# 🚀 Быстрый старт: Тест смены тарифа

## Что было добавлено:

✅ Новый тип теста `tariff_change` в test_engine.py
✅ Автоматическая смена тарифа с полной валидацией
✅ Проверка лимитов по productId
✅ Проверка списания с баланса
✅ Проверка доступных тарифов и пакетов

## Как использовать:

### 1. Запуск сервера:
```bash
python server.py
```

### 2. Открыть в браузере:
```
http://localhost:5000/tester
```

### 3. Пример запроса через curl:

```bash
curl -X POST http://localhost:5000/api/test/run \
  -H "Content-Type: application/json" \
  -d '{
    "msisdn": "998500173054",
    "testType": "tariff_change",
    "targetName": "Barcha 65",
    "expected": {
      "targetRatePlanId": 123,
      "targetProductId": 258,
      "minutesLimit": 1000,
      "smsLimit": 1000,
      "internetGb": 10,
      "expectedBalanceCharge": 65000,
      "expectedNextFee": 65000,
      "waitTimeSeconds": 5
    }
  }'
```

## Что проверяется автоматически:

1. ✅ **Баланс ДО** смены тарифа
2. ✅ **Поиск** целевого тарифа в списке доступных
3. ✅ **Смена** тарифа через API
4. ✅ **Ожидание** применения (5 сек)
5. ✅ **Проверка productId** - тариф изменился
6. ✅ **Лимиты** - минуты, SMS, интернет (по productId в rtDiscounts)
7. ✅ **Списание** с баланса (сколько сняло)
8. ✅ **След. АП** - следующая абонплата
9. ✅ **Доступные тарифы** после смены
10. ✅ **Доступные пакеты** после смены

## Минимальный пример:

```json
{
  "msisdn": "998500173054",
  "testType": "tariff_change",
  "expected": {
    "targetRatePlanName": "Barcha 65",
    "minutesLimit": 1000,
    "smsLimit": 1000,
    "internetGb": 10
  }
}
```

## Полный пример с всеми проверками:

```json
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
    "availableRatePlanNamesAfter": ["Barcha 95", "Barcha 125"],
    "availablePacksCountAfter": 20,
    "availablePackNamesAfter": ["Пакет 1", "Пакет 2"]
  }
}
```

## Параметры:

| Параметр | Обязательно | Описание |
|----------|-------------|----------|
| `targetRatePlanId` | Да* | ID тарифа для смены |
| `targetRatePlanName` | Да* | Название тарифа (альтернатива ID) |
| `targetProductId` | Нет | Product ID для проверки в rtDiscounts |
| `minutesLimit` | Нет | Ожидаемые минуты |
| `smsLimit` | Нет | Ожидаемые SMS |
| `internetGb` | Нет | Ожидаемый интернет (ГБ) |
| `expectedBalanceCharge` | Нет | Ожидаемое списание |
| `expectedNextFee` | Нет | Ожидаемая след. АП |
| `waitTimeSeconds` | Нет | Время ожидания (по умолчанию 5) |

*Нужен либо `targetRatePlanId`, либо `targetRatePlanName`

## Результат:

Тест вернет детальный отчет с проверками:
- ✅ PASS - проверка прошла
- ❌ FAIL - проверка не прошла
- ⚠️ WARN - предупреждение
- ⏭️ SKIP - пропущено
- ❗ ERROR - ошибка

## Просмотр результатов:

1. В ответе API
2. В файле `test_history/YYYYMMDD_HHMMSS_MSISDN.json`
3. Через `http://localhost:5000/tester` (история тестов)

## Следующие шаги:

Для добавления UI в tester.html нужно:
1. Добавить опцию "Смена тарифа" в выбор типа теста
2. Добавить поля для ввода параметров смены
3. Добавить выпадающий список доступных тарифов

Хотите, чтобы я обновил tester.html?
