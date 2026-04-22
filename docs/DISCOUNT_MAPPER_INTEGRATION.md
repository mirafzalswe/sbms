# Интеграция маппинга discountPlanId → описание из Excel

## 📋 Описание изменений

Реализована интеграция Excel-файла `DISPID.xlsx` с системой для автоматического отображения **корректных названий скидок** в блоке "Лимиты текущего тарифа".

## ✅ Что было сделано

### 1. Создан модуль `discount_mapper.py`

**Функционал:**
- Загрузка маппинга `discountPlanId → описание` из Excel-файла `DISPID.xlsx`
- Singleton pattern для эффективного использования памяти
- Автоматическая очистка неразрывных пробелов (`\xa0`) в описаниях
- Поддержка 2041+ записи маппинга

**Основные функции:**
```python
from discount_mapper import get_discount_description

# Получить описание по ID
description = get_discount_description(101)
# Вернет: "Общий интернет в рамках ТП"
```

**Класс DiscountMapper:**
```python
from discount_mapper import get_discount_mapper

mapper = get_discount_mapper()
print(mapper.is_loaded())  # True/False
print(len(mapper.get_all_mappings()))  # 2041
description = mapper.get_description(281)  # "Общий интернет в рамках пакета"
```

### 2. Обновлен модуль `sbms_checks.py`

**Изменения в функциях:**

#### `extract_volumes(rt_discounts_data)`
Добавлены новые поля в `details`:
- `discountPlanId` - ID плана скидки из API
- `discountDescription` - описание из Excel-файла

**До:**
```python
details.append({
    "measureUnitId": uid,
    "unit": MEASURE_UNITS.get(uid, f"unit_{uid}"),
    "maxVolume": max_vol,
    "spentVolume": spent,
    "remaining": remaining,
    "endDate": item.get("endDate", ""),
    "discountName": item.get("discountName", ""),
})
```

**После:**
```python
# Получаем описание из Excel по discountPlanId
discount_plan_id = item.get("discountPlanId")
discount_description = get_discount_description(discount_plan_id) if discount_plan_id else ""

details.append({
    "measureUnitId": uid,
    "unit": MEASURE_UNITS.get(uid, f"unit_{uid}"),
    "maxVolume": max_vol,
    "spentVolume": spent,
    "remaining": remaining,
    "endDate": item.get("endDate", ""),
    "discountPlanId": discount_plan_id,
    "discountName": item.get("discountName", ""),
    "discountDescription": discount_description,  # ← НОВОЕ ПОЛЕ
})
```

#### `extract_volumes_by_product_id(rt_discounts_data, product_id)`
Аналогичные изменения для фильтрованного извлечения по productId.

### 3. Обновлен интерфейс `tariff_test.html`

**Изменения в отображении:**

**До:**
```javascript
const discountLabel = d.discountName || '—';
```

**После:**
```javascript
// Приоритет: discountDescription (из Excel) > discountName (из API) > прочерк
const discountLabel = d.discountDescription || d.discountName || '—';
const discountIdLabel = d.discountPlanId ? ` <span style="color:#64748b;font-size:11px;">(ID: ${d.discountPlanId})</span>` : '';
```

**Результат:**
Теперь в колонке "Название скидки" отображается:
1. **Приоритет 1:** Описание из Excel (`discountDescription`)
2. **Приоритет 2:** Название из API (`discountName`)
3. **Fallback:** Прочерк `—`

Дополнительно показывается `discountPlanId` серым цветом.

### 4. Обновлен `requirements.txt`

Добавлена зависимость:
```
openpyxl>=3.0.0
```

Для установки:
```bash
pipenv install openpyxl
```

## 📊 Структура Excel-файла `DISPID.xlsx`

| Колонка | Название     | Описание                             |
|---------|--------------|--------------------------------------|
| A       | DCPL_ID      | ID плана скидки (discountPlanId)     |
| B       | DEF          | Описание скидки/тарифа               |

**Пример данных:**
```
DCPL_ID | DEF
--------|----------------------------------------
17      | Cost Center DATA BALANCE
100     | Hot billing (local)
101     | Общий интернет в рамках ТП
170     | Cost Center DATA LIMIT
281     | Общий интернет в рамках пакета
282     | Минуты по Узбекистану по доп. па
283     | SMS по Узбекистану по доп. пакет
654     | VOICE BALANCE 1
```

**Всего записей:** 2041

## 🧪 Тестирование

### Автоматический тест

Создан файл `test_discount_mapper.py` для проверки интеграции.

**Запуск:**
```bash
pipenv run python test_discount_mapper.py
```

**Результаты теста:**
```
✅ Маппер загружен: True
✅ Записей в маппинге: 2041

ТЕСТ 1: extract_volumes()
- ✅ Описание найдено для discountPlanId=101: "Общий интернет в рамках ТП"
- ✅ Описание найдено для discountPlanId=281: "Общий интернет в рамках пакета"
- ✅ Описание найдено для discountPlanId=654: "VOICE BALANCE 1"
- ❌ Описание не найдено для discountPlanId=999999 (не в Excel)

ТЕСТ 2: extract_volumes_by_product_id()
- ✅ Фильтрация по productId работает корректно
- ✅ Описания добавляются для каждой записи
```

### Ручное тестирование через веб-интерфейс

1. **Запустите сервер:**
   ```bash
   pipenv run python server.py
   ```

2. **Откройте в браузере:**
   ```
   http://localhost:5000/tariff-test
   ```

3. **Загрузите клиента:**
   - Введите MSISDN: `998500173054`
   - Нажмите "Загрузить"

4. **Проверьте блок "Лимиты текущего тарифа":**
   - В колонке "Название скидки" должны отображаться описания из Excel
   - Рядом с описанием должен быть серый текст `(ID: XXX)` с discountPlanId

## 📈 Результат

### До изменений
```
Тип       | Название скидки | Выдано | Использовано | Остаток
----------|-----------------|--------|--------------|----------
Интернет  | —               | 10 ГБ  | 2 ГБ         | 8 ГБ
Минуты    | —               | 500    | 100          | 400
SMS       | —               | 200    | 50           | 150
```

### После изменений
```
Тип       | Название скидки                           | Выдано | Использовано | Остаток
----------|-------------------------------------------|--------|--------------|----------
Интернет  | Общий интернет в рамках ТП (ID: 101)      | 10 ГБ  | 2 ГБ         | 8 ГБ
Минуты    | Общий интернет в рамках пакета (ID: 281)  | 500    | 100          | 400
SMS       | VOICE BALANCE 1 (ID: 654)                 | 200    | 50           | 150
```

## 🔍 Примеры использования API

### Python (backend)
```python
from sbms_client import SBMSClient
from sbms_checks import extract_volumes
from discount_mapper import get_discount_mapper

# Инициализация
client = SBMSClient("https://sbms.ucell", timeout=30)
client.authenticate("LOGIN", "PASSWORD")

# Получение rtDiscounts
subscriber_id = 1252275098
rt_data = client.get_rt_discounts(subscriber_id)

# Извлечение объемов с описаниями
totals, details = extract_volumes(rt_data)

# Вывод результатов
for item in details:
    print(f"{item['unit']}: {item['maxVolume']}")
    print(f"  Plan ID: {item['discountPlanId']}")
    print(f"  Описание: {item['discountDescription']}")
    print(f"  Остаток: {item['remaining']}")
```

### JavaScript (frontend)
```javascript
// Загрузка данных клиента
const response = await fetch('/api/tariff/load-customer', {
    method: 'POST',
    body: JSON.stringify({ msisdn: '998500173054' })
});

const data = await response.json();

// Отображение лимитов
const details = data.customer.volumesByTariff_details || [];
details.forEach(d => {
    const discountName = d.discountDescription || d.discountName || '—';
    const discountId = d.discountPlanId || '';
    
    console.log(`${d.unit}: ${d.maxVolume} (${discountName}, ID: ${discountId})`);
});
```

## 🐛 Обработка ошибок

### Файл DISPID.xlsx не найден
```
⚠️  Файл DISPID.xlsx не найден. Маппинг discountPlanId недоступен.
```
**Решение:** Убедитесь, что файл `DISPID.xlsx` находится в корне проекта.

### discountPlanId не найден в Excel
```
discountPlanId: 999999
Описание из Excel: ''
```
**Решение:** Это нормальное поведение. Для новых/отсутствующих ID будет пустая строка, и отобразится fallback (`discountName` или `—`).

### Ошибка загрузки Excel
```
⚠️  Ошибка при загрузке DISPID.xlsx: [детали ошибки]
```
**Решение:** Проверьте целостность файла и установите `openpyxl`:
```bash
pipenv install openpyxl
```

## 📝 Обслуживание

### Обновление маппинга

1. **Обновить файл `DISPID.xlsx`:**
   - Добавить новые записи в формате: `DCPL_ID | DEF`
   - Сохранить файл

2. **Перезапустить сервер:**
   ```bash
   # Остановить текущий сервер (Ctrl+C)
   pipenv run python server.py
   ```

3. **Проверить загрузку:**
   В логах сервера должно появиться:
   ```
   ✅ Загружено 2041+ маппингов discountPlanId из DISPID.xlsx
   ```

### Проверка актуальности маппинга

```bash
pipenv run python discount_mapper.py
```

Вывод покажет:
- Количество загруженных записей
- Примеры маппингов для тестовых ID

## 🎯 Преимущества реализации

1. ✅ **Централизованное управление:** Все описания в одном Excel-файле
2. ✅ **Простота обновления:** Не требуется изменение кода, только обновление Excel
3. ✅ **Производительность:** Singleton pattern, загрузка один раз при старте
4. ✅ **Fallback механизм:** Если описание не найдено, используется API данные
5. ✅ **Типобезопасность:** Автоматическая очистка спецсимволов (`\xa0`)
6. ✅ **Информативность:** Отображение discountPlanId рядом с описанием

## 🔗 Файлы изменений

| Файл                        | Статус     | Описание                                  |
|-----------------------------|------------|-------------------------------------------|
| `discount_mapper.py`        | ✅ Создан  | Модуль загрузки и работы с маппингом      |
| `sbms_checks.py`            | ✅ Изменен | Добавлены поля discountPlanId, discountDescription |
| `tariff_test.html`          | ✅ Изменен | Отображение описаний в UI                 |
| `requirements.txt`          | ✅ Изменен | Добавлена зависимость openpyxl            |
| `test_discount_mapper.py`   | ✅ Создан  | Автоматический тест интеграции            |
| `server.py`                 | ✅ Без изм.| Использует обновленные функции            |
| `sbms_runner.py`            | ✅ Без изм.| Использует обновленные функции            |

## 📞 Поддержка

При возникновении проблем:
1. Проверьте логи сервера на наличие ошибок загрузки Excel
2. Запустите тестовый скрипт: `pipenv run python test_discount_mapper.py`
3. Убедитесь, что файл `DISPID.xlsx` находится в корне проекта
4. Проверьте версию openpyxl: `pipenv graph | grep openpyxl`

---

**Дата реализации:** 20 февраля 2026 г.  
**Версия:** 1.0  
**Статус:** ✅ Реализовано и протестировано
