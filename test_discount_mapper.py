#!/usr/bin/env python3
"""
Тестовый скрипт для проверки интеграции discount_mapper
"""
from sbms_checks import extract_volumes, extract_volumes_by_product_id
from discount_mapper import get_discount_mapper

print("=" * 70)
print("  ТЕСТИРОВАНИЕ ИНТЕГРАЦИИ DISCOUNT_MAPPER")
print("=" * 70)
print()

# 1. Проверяем загрузку маппера
mapper = get_discount_mapper()
print(f"✅ Маппер загружен: {mapper.is_loaded()}")
print(f"✅ Записей в маппинге: {len(mapper.get_all_mappings())}")
print()

# 2. Тестовые данные rtDiscounts (реалистичные примеры)
test_data = {
    'items': [
        {
            'discountPlanId': 101,
            'measureUnitId': 14,
            'maxVolume': 10240,
            'spentVolume': 2048,
            'endDate': '2026-03-20T23:59:59',
            'productId': 258
        },
        {
            'discountPlanId': 281,
            'measureUnitId': 1,
            'maxVolume': 500,
            'spentVolume': 100,
            'endDate': '2026-03-20T23:59:59',
            'productId': 258
        },
        {
            'discountPlanId': 654,
            'measureUnitId': 7,
            'maxVolume': 200,
            'spentVolume': 50,
            'endDate': '2026-03-20T23:59:59',
            'productId': 258
        },
        {
            'discountPlanId': 999999,  # Несуществующий ID
            'measureUnitId': 0,
            'maxVolume': 1000,
            'spentVolume': 0,
            'endDate': '2026-03-20T23:59:59',
            'productId': 258
        }
    ]
}

# 3. Тестируем extract_volumes()
print("=" * 70)
print("  ТЕСТ 1: extract_volumes()")
print("=" * 70)
totals, details = extract_volumes(test_data)

print(f"\nСуммарные объемы:")
print(f"  Минуты: {totals['minutes']}")
print(f"  SMS: {totals['sms']}")
print(f"  МБ: {totals['mb']}")
print(f"  Деньги: {totals['money']} сум")

print(f"\nДетали ({len(details)} записей):")
for i, d in enumerate(details, 1):
    disc_id = d.get('discountPlanId', 'N/A')
    unit = d.get('unit', 'N/A')
    max_vol = d.get('maxVolume', 0)
    desc = d.get('discountDescription', '')
    
    print(f"\n  [{i}] Тип: {unit}, Лимит: {max_vol}")
    print(f"      discountPlanId: {disc_id}")
    print(f"      Описание из Excel: '{desc}'")
    
    if desc:
        print(f"      ✅ Описание найдено")
    else:
        print(f"      ❌ Описание не найдено (ID не в Excel)")

# 4. Тестируем extract_volumes_by_product_id()
print("\n" + "=" * 70)
print("  ТЕСТ 2: extract_volumes_by_product_id(productId=258)")
print("=" * 70)
totals2, details2, count = extract_volumes_by_product_id(test_data, 258)

print(f"\nНайдено записей с productId=258: {count}")
print(f"\nСуммарные объемы (только productId=258):")
print(f"  Минуты: {totals2['minutes']}")
print(f"  SMS: {totals2['sms']}")
print(f"  МБ: {totals2['mb']}")
print(f"  Деньги: {totals2['money']} сум")

print(f"\nДетали ({len(details2)} записей):")
for i, d in enumerate(details2, 1):
    disc_id = d.get('discountPlanId', 'N/A')
    unit = d.get('unit', 'N/A')
    max_vol = d.get('maxVolume', 0)
    desc = d.get('discountDescription', '')
    
    print(f"\n  [{i}] {unit}: {max_vol}, Описание: '{desc[:50]}{'...' if len(desc) > 50 else ''}'")

# 5. Примеры конкретных маппингов
print("\n" + "=" * 70)
print("  ПРИМЕРЫ МАППИНГОВ ИЗ EXCEL")
print("=" * 70)
sample_ids = [17, 100, 101, 170, 281, 282, 283, 654, 999999]
for disc_id in sample_ids:
    desc = mapper.get_description(disc_id)
    if desc:
        print(f"  ✅ {disc_id:6d} -> {desc}")
    else:
        print(f"  ❌ {disc_id:6d} -> [НЕ НАЙДЕН]")

print("\n" + "=" * 70)
print("  ✅ ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
print("=" * 70)
