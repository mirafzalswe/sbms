












#!/usr/bin/env python3
"""
UCELL SBMS API - Автоматизированное тестирование
================================================
6 основных тестовых сценариев:
  1. Get Session (авторизация)
  2. Customer Search Base (поиск клиента)
  3. Subscriber rtDiscounts (остатки пакетов)
  4. Current Custom Attributes (атрибуты клиента)
  5. Inquiries Slave Properties (свойства обращений)
  6. Phone Numbers Free (свободные номера)

Запуск: python run_tests.py
"""

import requests
import urllib3
import xml.etree.ElementTree as ET
import json
import time
import sys
import os
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ============================================================
# КОНФИГУРАЦИЯ - измените при необходимости
# ============================================================
BASE_URL = os.getenv("SBMS_BASE_URL", "https://sbms.ucell")
LOGIN = os.getenv("SBMS_LOGIN", "DBS_CC_OPERATORS_PSO")
PASSWORD = os.getenv("SBMS_PASSWORD", "Ucell2026$$")
TEST_MSISDN = os.getenv("TEST_MSISDN", "998500173054")
TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))

# ============================================================
# ЦВЕТА ТЕРМИНАЛА
# ============================================================
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
CYAN = '\033[96m'
BOLD = '\033[1m'
DIM = '\033[2m'
END = '\033[0m'

# ============================================================
# РЕЗУЛЬТАТЫ ТЕСТОВ
# ============================================================
results = []


def log_pass(test_name, details=""):
    results.append({"name": test_name, "status": "PASS", "details": details})
    print(f"  {GREEN}PASS{END}  {test_name}")
    if details:
        print(f"        {DIM}{details}{END}")


def log_fail(test_name, details=""):
    results.append({"name": test_name, "status": "FAIL", "details": details})
    print(f"  {RED}FAIL{END}  {test_name}")
    if details:
        print(f"        {RED}{details}{END}")


def header(text):
    print(f"\n{BOLD}{CYAN}{'=' * 65}{END}")
    print(f"{BOLD}{CYAN}  {text}{END}")
    print(f"{BOLD}{CYAN}{'=' * 65}{END}")


def subheader(text):
    print(f"\n  {YELLOW}--- {text} ---{END}")


# ============================================================
# ТЕСТ 1: GET SESSION
# ============================================================
def test_get_session():
    """
    Получение токена авторизации.
    URL: GET /OAPI/v1/tokens-stub/get?login=...&password=...
    Ответ: XML с полем SESSION_ID
    """
    header("ТЕСТ 1: GET SESSION (Авторизация)")
    print(f"  URL: {BASE_URL}/OAPI/v1/tokens-stub/get")
    print(f"  Login: {LOGIN}")

    try:
        url = f"{BASE_URL}/OAPI/v1/tokens-stub/get"
        params = {"login": LOGIN, "password": PASSWORD}

        start = time.time()
        resp = requests.get(url, params=params, timeout=TIMEOUT, verify=False)
        elapsed = time.time() - start

        print(f"  HTTP Status: {resp.status_code}")
        print(f"  Response Time: {elapsed:.2f}s")

        if resp.status_code != 200:
            log_fail("Авторизация", f"HTTP {resp.status_code}")
            return None

        body = resp.text.strip()
        if not body:
            log_fail("Авторизация", "Пустое тело ответа")
            return None

        # Попытка: JSON ответ
        if body.startswith("{") or body.startswith("["):
            try:
                data = resp.json()
                sid = data.get("SESSION_ID") or data.get("session_id") or data.get("sessionId") or data.get("token")
                if sid:
                    token = str(sid)
                    log_pass("Авторизация", f"Token (JSON): {token[:25]}...")
                    return token
                log_fail("Авторизация", f"SESSION_ID не найден в JSON: {list(data.keys())}")
                return None
            except Exception:
                log_fail("Авторизация", f"Невалидный JSON: {body[:200]}")
                return None

        # Стандартный путь: XML ответ
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            log_fail("Авторизация", f"Невалидный XML: {body[:200]}")
            return None

        session_el = root.find("SESSION_ID") or root.find(".//SESSION_ID")

        if session_el is not None and session_el.text:
            token = session_el.text
            log_pass("Авторизация", f"Token: {token[:25]}...")
            return token

        # Fallback: любой тег с длинным текстом (возможный токен)
        for child in root.iter():
            if child.text and len(child.text.strip()) > 10:
                token = child.text.strip()
                log_pass("Авторизация", f"Token (из <{child.tag}>): {token[:25]}...")
                return token

        log_fail("Авторизация", f"SESSION_ID не найден. Root: <{root.tag}>, теги: {[c.tag for c in root]}")
        print(f"  Response: {body[:300]}")
        return None

    except requests.exceptions.ConnectionError:
        log_fail("Авторизация", "Нет подключения к серверу")
        return None
    except Exception as e:
        log_fail("Авторизация", str(e))
        return None


# ============================================================
# ТЕСТ 2: CUSTOMER SEARCH BASE
# ============================================================
def test_customer_search(token):
    """
    Поиск клиента по номеру телефона (MSISDN).
    URL: GET /OAPI/v1/customers/searchBase?identification=...&authToken=...
    Ответ: JSON с данными клиента
    """
    header("ТЕСТ 2: CUSTOMER SEARCH BASE (Поиск клиента)")
    print(f"  MSISDN: {TEST_MSISDN}")

    url = f"{BASE_URL}/OAPI/v1/customers/searchBase"
    params = {"identification": TEST_MSISDN, "authToken": token}

    try:
        start = time.time()
        resp = requests.get(url, params=params, timeout=TIMEOUT, verify=False)
        elapsed = time.time() - start

        print(f"  HTTP Status: {resp.status_code}")
        print(f"  Response Time: {elapsed:.2f}s")

        if resp.status_code != 200:
            log_fail("Поиск клиента", f"HTTP {resp.status_code}")
            return None, None

        data = resp.json()
        count = data.get("listInfo", {}).get("count", 0)

        if count == 0:
            log_fail("Поиск клиента", "Клиент не найден")
            return None, None

        result = data["searchResults"][0]
        customer_id = result.get("customerId")
        subscriber_id = result.get("subscriberId")
        name = result.get("customer", {}).get("name", "N/A")
        rate_plan = result.get("firstSubscriber", {}).get("ratePlan", {}).get("name", "N/A")
        status = result.get("firstSubscriber", {}).get("status", {}).get("name", "N/A")

        log_pass("Поиск клиента", f"Найден: {name}")

        subheader("Результат поиска")
        print(f"  Customer ID:   {customer_id}")
        print(f"  Subscriber ID: {subscriber_id}")
        print(f"  ФИО:           {name}")
        print(f"  Тариф:         {rate_plan}")
        print(f"  Статус:        {status}")

        return customer_id, subscriber_id

    except Exception as e:
        log_fail("Поиск клиента", str(e))
        return None, None


# ============================================================
# ТЕСТ 3: SUBSCRIBER RT DISCOUNTS (Остатки пакетов)
# ============================================================
def test_rt_discounts(token, subscriber_id):
    """
    Получение остатков пакетов абонента.
    URL: GET /PSAPI/v1/bis-brt-balance/subscribers/{id}/rtDiscounts
    Параметры: authToken, customerDatabaseId=902
    """
    header("ТЕСТ 3: RT DISCOUNTS (Остатки пакетов)")
    print(f"  Subscriber ID: {subscriber_id}")

    url = f"{BASE_URL}/PSAPI/v1/bis-brt-balance/subscribers/{subscriber_id}/rtDiscounts"
    params = {
        "authToken": token,
        "customerDatabaseId": 902
    }

    try:
        start = time.time()
        resp = requests.get(url, params=params, timeout=TIMEOUT, verify=False)
        elapsed = time.time() - start

        print(f"  HTTP Status: {resp.status_code}")
        print(f"  Response Time: {elapsed:.2f}s")

        if resp.status_code != 200:
            log_fail("Остатки пакетов", f"HTTP {resp.status_code}")
            return

        data = resp.json()
        items = data.get("items", [])

        log_pass("Остатки пакетов", f"Найдено {len(items)} записей")

        # Расшифровка единиц измерения
        units = {0: "сум", 1: "минут", 7: "SMS", 14: "МБ"}

        subheader("Остатки пакетов")
        for item in items:
            unit_id = item.get("measureUnitId", 0)
            unit = units.get(unit_id, f"unit_{unit_id}")
            spent = item.get("spentVolume", 0)
            max_vol = item.get("maxVolume", 0)
            end_date = item.get("endDate", "N/A")

            # Рассчитываем остаток
            remaining = max_vol - spent
            if max_vol > 0 and max_vol < 999999:
                pct = (remaining / max_vol) * 100
                bar_len = 20
                filled = int(bar_len * remaining / max_vol)
                bar = f"[{'#' * filled}{'.' * (bar_len - filled)}]"
                print(f"    {bar} {remaining:.0f}/{max_vol:.0f} {unit} ({pct:.0f}%) до {end_date[:10]}")
            elif max_vol >= 999999:
                print(f"    [####################] Безлимит {unit} до {end_date[:10]}")

    except Exception as e:
        log_fail("Остатки пакетов", str(e))


# ============================================================
# ТЕСТ 4: CURRENT CUSTOM ATTRIBUTES
# ============================================================
def test_custom_attributes(token, customer_id):
    """
    Получение пользовательских атрибутов клиента.
    URL: GET /OAPI/v1/customers/{id}/currentCustomAttributes?authToken=...
    """
    header("ТЕСТ 4: CUSTOM ATTRIBUTES (Атрибуты клиента)")
    print(f"  Customer ID: {customer_id}")

    url = f"{BASE_URL}/OAPI/v1/customers/{customer_id}/currentCustomAttributes"
    params = {"authToken": token}

    try:
        start = time.time()
        resp = requests.get(url, params=params, timeout=TIMEOUT, verify=False)
        elapsed = time.time() - start

        print(f"  HTTP Status: {resp.status_code}")
        print(f"  Response Time: {elapsed:.2f}s")

        if resp.status_code != 200:
            log_fail("Атрибуты клиента", f"HTTP {resp.status_code}")
            return

        data = resp.json()
        items = data.get("items", [])

        log_pass("Атрибуты клиента", f"Найдено {len(items)} атрибутов")

        subheader("Атрибуты клиента")
        for item in items:
            attr_id = item.get("customAttributeId")
            name = item.get("name", "N/A")
            data_type = item.get("dataType", "N/A")
            values = item.get("values", [])

            # Извлекаем значение
            if values:
                val = values[0].get("value", "")
                if isinstance(val, dict):
                    val = val.get("name", str(val))
            else:
                val = "(пусто)"

            print(f"    [{attr_id}] {name}: {val} ({data_type})")

    except Exception as e:
        log_fail("Атрибуты клиента", str(e))


# ============================================================
# ТЕСТ 5: INQUIRIES SLAVE CUSTOM PROPERTIES
# ============================================================
def test_inquiry_properties(token):
    """
    Получение зависимых свойств обращений.
    URL: POST /OAPI/v1/inquiries/slaveCustomProperties?masterCustomPropertyDeclarationId=1045
    """
    header("ТЕСТ 5: INQUIRY PROPERTIES (Свойства обращений)")

    url = f"{BASE_URL}/OAPI/v1/inquiries/slaveCustomProperties"
    params = {
        "masterCustomPropertyDeclarationId": 1045,
        "AuthToken": token
    }

    # Тело запроса из Postman коллекции
    body = {
        "inquiry": {
            "topic": {
                "topicId": 365,
                "topicCode": "UCELL:CONTRACT:FO:B2C"
            },
            "description": "",
            "phone": None,
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
                },
                {
                    "customPropertyDeclaration": {
                        "customPropertyDeclarationId": 1085,
                        "customPropertyDeclarationCode": "UCELL:CONTRACT:CHECK_SUBCAT"
                    },
                    "type": "DB_QUERY",
                    "values": [{"value": "0"}]
                }
            ]
        },
        "contact": {
            "direction": "INCOMING",
            "contactSite": {"contactSiteId": 21, "contactSiteCode": "OFFICE_CONTACT"},
            "channel": {"channelId": 2, "channelCode": "VISIT"}
        }
    }

    try:
        start = time.time()
        resp = requests.post(url, params=params, json=body, timeout=TIMEOUT, verify=False)
        elapsed = time.time() - start

        print(f"  HTTP Status: {resp.status_code}")
        print(f"  Response Time: {elapsed:.2f}s")

        if resp.status_code != 200:
            log_fail("Свойства обращений", f"HTTP {resp.status_code}")
            return

        data = resp.json()

        prop_values = data.get("customPropertyValues", [])
        prop_access = data.get("customPropertyAccessibilities", [])

        log_pass("Свойства обращений", f"{len(prop_values)} свойств, {len(prop_access)} правил доступа")

        subheader("Правила доступности полей")
        for acc in prop_access:
            name = acc.get("name", "N/A")
            accessibility = acc.get("accessibility", "N/A")
            icon = "(*)" if accessibility == "MANDATORY" else "(-)" if accessibility == "HIDDEN" else "(o)"
            print(f"    {icon} {name}: {accessibility}")

    except Exception as e:
        log_fail("Свойства обращений", str(e))


# ============================================================
# ТЕСТ 6: PHONE NUMBERS FREE
# ============================================================
def test_free_phone_numbers(token):
    """
    Получение списка свободных номеров.
    URL: GET /OAPI/v1/networkResources/phoneNumbers/free?standardId=1&freeMonthPeriod=2
    """
    header("ТЕСТ 6: FREE PHONE NUMBERS (Свободные номера)")

    url = f"{BASE_URL}/OAPI/v1/networkResources/phoneNumbers/free"
    params = {
        "authToken": token,
        "standardId": 1,
        "freeMonthPeriod": 2
    }

    try:
        start = time.time()
        resp = requests.get(url, params=params, timeout=TIMEOUT, verify=False)
        elapsed = time.time() - start

        print(f"  HTTP Status: {resp.status_code}")
        print(f"  Response Time: {elapsed:.2f}s")

        if resp.status_code != 200:
            log_fail("Свободные номера", f"HTTP {resp.status_code}")
            return

        data = resp.json()
        total = data.get("listInfo", {}).get("count", 0)
        items = data.get("items", [])

        log_pass("Свободные номера", f"Всего: {total}, показано: {len(items)}")

        # Группируем по классу номера
        classes = {}
        for item in items:
            cls_name = item.get("numberClass", {}).get("name", "N/A")
            charge = item.get("phoneNumberCharge", {}).get("amount", 0)
            if cls_name not in classes:
                classes[cls_name] = {"count": 0, "charge": charge}
            classes[cls_name]["count"] += 1

        subheader("По классам номеров")
        for cls_name, info in sorted(classes.items(), key=lambda x: x[1]["charge"]):
            charge_str = f"{info['charge']:,.0f} сум" if info["charge"] > 0 else "бесплатно"
            print(f"    {cls_name}: {info['count']} шт. ({charge_str})")

        # Показываем первые 5 номеров
        subheader("Примеры номеров (первые 5)")
        for item in items[:5]:
            phone = item.get("phoneNumber", "N/A")
            cls_name = item.get("numberClass", {}).get("name", "N/A")
            print(f"    +{phone} [{cls_name}]")

    except Exception as e:
        log_fail("Свободные номера", str(e))


# ============================================================
# ИТОГИ
# ============================================================
def print_summary():
    header("ИТОГИ ТЕСТИРОВАНИЯ")

    total = len(results)
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")

    print(f"\n  Всего тестов:  {total}")
    print(f"  {GREEN}Пройдено:      {passed}{END}")
    if failed > 0:
        print(f"  {RED}Провалено:     {failed}{END}")
    else:
        print(f"  Провалено:     0")

    pct = (passed / total * 100) if total > 0 else 0
    print(f"\n  Успешность:    {pct:.0f}%")

    # Сохраняем JSON отчет
    report = {
        "timestamp": datetime.now().isoformat(),
        "base_url": BASE_URL,
        "test_msisdn": TEST_MSISDN,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "success_rate": f"{pct:.0f}%"
        },
        "tests": results
    }

    report_path = os.path.join(os.path.dirname(__file__), "test_results.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"\n  Отчет сохранен: {report_path}")

    if failed > 0:
        print(f"\n  {RED}Провалены:{END}")
        for r in results:
            if r["status"] == "FAIL":
                print(f"    {RED}- {r['name']}: {r['details']}{END}")

    print()
    return failed == 0


# ============================================================
# MAIN
# ============================================================
def main():
    print(f"\n{BOLD}{'=' * 65}{END}")
    print(f"{BOLD}  UCELL SBMS API - АВТОМАТИЗИРОВАННОЕ ТЕСТИРОВАНИЕ{END}")
    print(f"{BOLD}{'=' * 65}{END}")
    print(f"  Дата:     {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Сервер:   {BASE_URL}")
    print(f"  Логин:    {LOGIN}")
    print(f"  MSISDN:   {TEST_MSISDN}")
    print(f"  Timeout:  {TIMEOUT}s")

    # ТЕСТ 1: Авторизация
    token = test_get_session()
    if not token:
        print(f"\n{RED}  Авторизация не удалась. Остальные тесты пропущены.{END}\n")
        print_summary()
        sys.exit(1)

    # ТЕСТ 2: Поиск клиента
    customer_id, subscriber_id = test_customer_search(token)

    # ТЕСТ 3: Остатки пакетов (если найден абонент)
    if subscriber_id:
        test_rt_discounts(token, subscriber_id)
    else:
        log_fail("Остатки пакетов", "Subscriber ID не получен из теста 2")

    # ТЕСТ 4: Атрибуты клиента (если найден клиент)
    if customer_id:
        test_custom_attributes(token, customer_id)
    else:
        log_fail("Атрибуты клиента", "Customer ID не получен из теста 2")

    # ТЕСТ 5: Свойства обращений
    test_inquiry_properties(token)

    # ТЕСТ 6: Свободные номера
    test_free_phone_numbers(token)

    # Итоги
    success = print_summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}  Тестирование прервано пользователем{END}\n")
        sys.exit(130)
