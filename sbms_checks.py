"""
SBMS Checks & Models
=====================
Модели данных, функции сравнения, извлечение объёмов, работа с историей.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Optional

# Импорт модуля для работы с маппингом discountPlanId
try:
    from discount_mapper import get_discount_description
except ImportError:
    # Fallback, если модуль не найден
    def get_discount_description(discount_plan_id):
        return ""


# Единицы измерения из SBMS
MEASURE_UNITS = {0: "сум", 1: "минуты", 7: "SMS", 14: "МБ"}


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class CheckResult:
    name: str
    category: str
    status: str  # PASS, FAIL, WARN, SKIP, ERROR
    expected: object = None
    actual: object = None
    message: str = ""
    api_endpoint: str = ""

    def to_dict(self):
        return {
            "name": self.name,
            "category": self.category,
            "status": self.status,
            "expected": self.expected,
            "actual": self.actual,
            "message": self.message,
            "apiEndpoint": self.api_endpoint
        }


@dataclass
class TestReport:
    test_id: str = ""
    timestamp: str = ""
    duration: float = 0
    msisdn: str = ""
    test_type: str = ""
    target_name: str = ""
    customer_info: dict = field(default_factory=dict)
    checks: list = field(default_factory=list)
    raw_responses: dict = field(default_factory=dict)
    error: str = ""

    @property
    def summary(self):
        total = len(self.checks)
        passed = sum(1 for c in self.checks if c.status == "PASS")
        failed = sum(1 for c in self.checks if c.status == "FAIL")
        warnings = sum(1 for c in self.checks if c.status == "WARN")
        skipped = sum(1 for c in self.checks if c.status == "SKIP")
        errors = sum(1 for c in self.checks if c.status == "ERROR")
        return {
            "total": total, "passed": passed, "failed": failed,
            "warnings": warnings, "skipped": skipped, "errors": errors
        }

    def to_dict(self):
        return {
            "testId": self.test_id,
            "timestamp": self.timestamp,
            "duration": round(self.duration, 2),
            "msisdn": self.msisdn,
            "testType": self.test_type,
            "targetName": self.target_name,
            "customerInfo": self.customer_info,
            "summary": self.summary,
            "checks": [c.to_dict() for c in self.checks],
            "rawResponses": self.raw_responses,
            "error": self.error
        }


# ============================================================
# COMPARISON HELPERS
# ============================================================

def compare_numeric(expected, actual, name, unit="", tolerance_pct=1):
    """Сравнить числовые значения с допуском."""
    if expected is None or expected == "":
        return CheckResult(name, "value", "SKIP", expected, actual, "Не задано ожидаемое значение")

    try:
        exp = float(expected)
        act = float(actual) if actual is not None else 0
    except (TypeError, ValueError):
        return CheckResult(name, "value", "ERROR", expected, actual, f"Невозможно сравнить: expected={expected}, actual={actual}")

    if exp == 0 and act == 0:
        return CheckResult(name, "value", "PASS", f"{exp:.0f} {unit}", f"{act:.0f} {unit}")

    diff = abs(act - exp)
    tolerance = exp * tolerance_pct / 100 if exp != 0 else 0

    if diff <= tolerance:
        return CheckResult(name, "value", "PASS", f"{exp:.0f} {unit}", f"{act:.0f} {unit}")

    pct = (diff / exp * 100) if exp != 0 else 100
    return CheckResult(
        name, "value", "FAIL",
        f"{exp:.0f} {unit}", f"{act:.0f} {unit}",
        f"Разница: {diff:.0f} {unit} ({pct:.1f}%)"
    )


def compare_string(expected, actual, name):
    """Сравнить строки (case-insensitive, partial match)."""
    if not expected:
        return CheckResult(name, "info", "SKIP", expected, actual, "Не задано")

    exp_lower = str(expected).lower().strip()
    act_lower = str(actual).lower().strip() if actual else ""

    if exp_lower in act_lower or act_lower in exp_lower:
        return CheckResult(name, "info", "PASS", expected, actual)

    return CheckResult(name, "info", "FAIL", expected, actual,
                       f"Ожидалось '{expected}', получено '{actual}'")


def compare_list_count(expected_count, actual_list, name, unit="шт"):
    """Сравнить количество элементов в списке."""
    if expected_count is None or expected_count == "":
        return CheckResult(name, "count", "SKIP", None, len(actual_list), "Не задано ожидаемое количество")

    try:
        exp = int(expected_count)
    except (TypeError, ValueError):
        return CheckResult(name, "count", "ERROR", expected_count, len(actual_list), "Некорректное ожидаемое значение")

    act = len(actual_list)
    if act == exp:
        return CheckResult(name, "count", "PASS", f"{exp} {unit}", f"{act} {unit}")

    return CheckResult(name, "count", "FAIL", f"{exp} {unit}", f"{act} {unit}",
                       f"Ожидалось {exp}, получено {act}")


def compare_list_names(expected_names, actual_items, name_field, check_name):
    """Проверить что ожидаемые имена присутствуют в фактическом списке."""
    if not expected_names:
        return CheckResult(check_name, "list", "SKIP", None, None, "Не задан список для проверки")

    expected = [n.strip() for n in expected_names if n.strip()]
    if not expected:
        return CheckResult(check_name, "list", "SKIP", None, None, "Пустой список")

    actual_names = [str(item.get(name_field, "")).lower() for item in actual_items]
    found = []
    missing = []

    for exp_name in expected:
        if any(exp_name.lower() in act for act in actual_names):
            found.append(exp_name)
        else:
            missing.append(exp_name)

    if not missing:
        return CheckResult(check_name, "list", "PASS",
                          ", ".join(expected), f"Найдены все {len(found)}")

    return CheckResult(check_name, "list", "FAIL",
                      ", ".join(expected),
                      f"Найдено {len(found)}/{len(expected)}",
                      f"Не найдены: {', '.join(missing)}")


# ============================================================
# VOLUME EXTRACTION
# ============================================================

def extract_volumes(rt_discounts_data):
    """Извлечь объёмы из ответа rtDiscounts."""
    items = rt_discounts_data.get("items", []) if rt_discounts_data else []

    totals = {"minutes": 0, "sms": 0, "mb": 0, "money": 0}
    details = []

    for item in items:
        uid = item.get("measureUnitId", -1)
        max_vol = item.get("maxVolume", 0) or 0
        spent = item.get("spentVolume", 0) or 0
        remaining = max_vol - spent

        if uid == 1:
            totals["minutes"] += max_vol
        elif uid == 7:
            totals["sms"] += max_vol
        elif uid == 14:
            totals["mb"] += max_vol
        elif uid == 0:
            totals["money"] += max_vol

        # Получаем описание из Excel по discountPlanId
        discount_plan_id = item.get("discountPlanId")
        discount_description = get_discount_description(discount_plan_id) if discount_plan_id else ""

        details.append({
            "measureUnitId": uid,
            "unit": MEASURE_UNITS.get(uid, f"unit_{uid}"),
            "maxVolume": max_vol,
            "spentVolume": spent,
            "remaining": remaining,
            "startDate": item.get("startDate", "") or item.get("beginDate", ""),
            "endDate": item.get("endDate", ""),
            "discountPlanId": discount_plan_id,
            "discountName": item.get("discountName", ""),
            "discountDescription": discount_description,
        })

    return totals, details


def extract_volumes_by_product_id(rt_discounts_data, product_id):
    """Извлечь объёмы из rtDiscounts, отфильтрованные по productId.

    При смене тарифа в rtDiscounts могут быть items от старого и нового тарифа.
    Фильтрация по productId даёт объёмы только текущего тарифа.
    """
    items = rt_discounts_data.get("items", []) if rt_discounts_data else []
    filtered = [item for item in items if item.get("productId") == product_id]

    totals = {"minutes": 0, "sms": 0, "mb": 0, "money": 0}
    details = []

    for item in filtered:
        uid = item.get("measureUnitId", -1)
        max_vol = item.get("maxVolume", 0) or 0
        spent = item.get("spentVolume", 0) or 0
        remaining = max_vol - spent

        if uid == 1:
            totals["minutes"] += max_vol
        elif uid == 7:
            totals["sms"] += max_vol
        elif uid == 14:
            totals["mb"] += max_vol
        elif uid == 0:
            totals["money"] += max_vol

        # Получаем описание из Excel по discountPlanId
        discount_plan_id = item.get("discountPlanId")
        discount_description = get_discount_description(discount_plan_id) if discount_plan_id else ""

        details.append({
            "productId": product_id,
            "measureUnitId": uid,
            "unit": MEASURE_UNITS.get(uid, f"unit_{uid}"),
            "maxVolume": max_vol,
            "spentVolume": spent,
            "remaining": remaining,
            "startDate": item.get("startDate", "") or item.get("beginDate", ""),
            "endDate": item.get("endDate", ""),
            "discountPlanId": discount_plan_id,
            "discountName": item.get("discountName", ""),
            "discountDescription": discount_description,
        })

    return totals, details, len(filtered)


# ============================================================
# CHARGE EXTRACTION (из ответов nextCharges)
# ============================================================

def find_recurring_charges(data):
    """Рекурсивно найти recurringCharges в любой вложенности ответа."""
    if isinstance(data, dict):
        if "recurringCharges" in data:
            rc = data["recurringCharges"]
            if isinstance(rc, list) and rc:
                return rc
        for val in data.values():
            result = find_recurring_charges(val)
            if result:
                return result
    elif isinstance(data, list):
        for item in data:
            result = find_recurring_charges(item)
            if result:
                return result
    return None


def extract_recurring_charge(nc_data):
    """Извлечь сумму абонплаты из ответа nextCharges.

    Рекурсивно ищет recurringCharges[0].amount в ответе любой структуры.
    """
    if nc_data is None:
        return None

    rc = find_recurring_charges(nc_data)
    if rc and isinstance(rc, list) and len(rc) > 0:
        return rc[0].get("amount")

    # Fallback: прямой amount на верхнем уровне
    if isinstance(nc_data, dict):
        return nc_data.get("amount")
    if isinstance(nc_data, list) and nc_data:
        return nc_data[0].get("amount") if isinstance(nc_data[0], dict) else None

    return None


def extract_product_id_from_charges(nc_data):
    """Извлечь productId (= subscriptionId в recurringCharges) из ответа nextCharges."""
    rc = find_recurring_charges(nc_data)
    if rc and isinstance(rc, list) and len(rc) > 0:
        return rc[0].get("subscriptionId")
    return None


# ============================================================
# HISTORY (сохранение/чтение отчётов)
# ============================================================

def save_report(report: TestReport, directory="test_history"):
    """Сохранить отчёт в файл."""
    base = os.path.dirname(os.path.abspath(__file__))
    hist_dir = os.path.join(base, directory)
    os.makedirs(hist_dir, exist_ok=True)

    filename = f"{report.test_id}.json"
    filepath = os.path.join(hist_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)

    return filename


def list_reports(directory="test_history"):
    """Список сохранённых отчётов."""
    base = os.path.dirname(os.path.abspath(__file__))
    hist_dir = os.path.join(base, directory)
    if not os.path.exists(hist_dir):
        return []

    reports = []
    for fn in sorted(os.listdir(hist_dir), reverse=True):
        if fn.endswith(".json"):
            filepath = os.path.join(hist_dir, fn)
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                reports.append({
                    "id": data.get("testId", fn.replace(".json", "")),
                    "timestamp": data.get("timestamp", ""),
                    "msisdn": data.get("msisdn", ""),
                    "testType": data.get("testType", ""),
                    "targetName": data.get("targetName", ""),
                    "summary": data.get("summary", {})
                })
            except Exception:
                pass

    return reports[:50]


def get_report(report_id, directory="test_history"):
    """Получить конкретный отчёт."""
    base = os.path.dirname(os.path.abspath(__file__))
    filepath = os.path.join(base, directory, f"{report_id}.json")
    if not os.path.exists(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)
