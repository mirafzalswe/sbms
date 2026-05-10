"""
SBMS Test Runner
=================
Оркестратор тестирования: запускает проверки тарифов, пакетов, услуг, смены тарифа.
"""

import time
from datetime import datetime

from sbms_client import SBMSClient
from sbms_checks import (
    CheckResult, TestReport,
    compare_numeric, compare_string, compare_list_count, compare_list_names,
    extract_volumes, extract_volumes_by_product_id,
    extract_recurring_charge, extract_product_id_from_charges,
)
try:
    from discount_mapper import get_discount_description as _get_disc_name
except ImportError:
    def _get_disc_name(pid): return ""


class TestRunner:
    """Оркестратор тестирования."""

    def __init__(self, base_url, login, password, timeout=30, auth_token=None):
        self.client = SBMSClient(base_url, timeout)
        self.login = login
        self.password = password
        self.auth_token = auth_token

    @staticmethod
    def _parse_api_error(text):
        """Извлечь userMessage из JSON-ошибки API."""
        if not text:
            return ""
        try:
            import json
            data = json.loads(text)
            return data.get("userMessage") or data.get("developerMessage") or data.get("message") or ""
        except (json.JSONDecodeError, TypeError):
            return ""

    def run(self, test_case: dict) -> TestReport:
        """Запуск полного теста. test_case — словарь из запроса."""
        start = time.time()
        report = TestReport()
        report.timestamp = datetime.now().isoformat()
        report.msisdn = test_case.get("msisdn", "")
        report.test_type = test_case.get("testType", "tariff")
        report.target_name = test_case.get("targetName", "")
        report.test_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + report.msisdn

        expected = test_case.get("expected", {})

        try:
            # 1. Авторизация (или переиспользование токена)
            if self.auth_token:
                self.client.token = self.auth_token
            else:
                self.client.authenticate(self.login, self.password)
            if report.test_type not in ("tariff_change", "pack_activate"):
                report.checks.append(CheckResult("Авторизация", "auth", "PASS",
                                                message=f"Token: {self.client.token[:20]}...",
                                                api_endpoint="/OAPI/v1/tokens-stub/get"))

            # 2. Поиск клиента
            search_data = self.client.search_customer(report.msisdn)
            report.raw_responses["searchBase"] = search_data

            if not search_data or search_data.get("listInfo", {}).get("count", 0) == 0:
                report.checks.append(CheckResult("Поиск клиента", "search", "FAIL",
                                                message="Клиент не найден"))
                report.duration = time.time() - start
                return report

            sr = search_data["searchResults"][0]
            cid = sr.get("customerId")
            sid = sr.get("subscriberId")
            cust_name = sr.get("customer", {}).get("name", "N/A")
            rate_plan = sr.get("firstSubscriber", {}).get("ratePlan", {})
            rp_name = rate_plan.get("name", "N/A")
            rp_id = rate_plan.get("ratePlanId")
            status_name = sr.get("firstSubscriber", {}).get("status", {}).get("name", "N/A")

            report.customer_info = {
                "customerId": cid,
                "subscriberId": sid,
                "name": cust_name,
                "ratePlanName": rp_name,
                "ratePlanId": rp_id,
                "status": status_name
            }

            if report.test_type not in ("tariff_change", "pack_activate"):
                report.checks.append(CheckResult("Поиск клиента", "search", "PASS",
                                                actual=f"{cust_name} | {rp_name}",
                                                api_endpoint="/OAPI/v1/customers/searchBase"))

            # Выбор потока теста
            if report.test_type == "tariff":
                self._run_tariff_checks(report, expected, cid, sid, rp_name, rp_id)
            elif report.test_type == "tariff_change":
                self._run_tariff_change_checks(report, expected, cid, sid)
            elif report.test_type == "pack_activate":
                self._run_pack_activate_checks(report, expected, cid, sid)
            elif report.test_type == "pack":
                self._run_pack_checks(report, expected, cid, sid)
            elif report.test_type == "service":
                self._run_service_checks(report, expected, cid, sid)

        except Exception as e:
            report.error = str(e)
            report.checks.append(CheckResult("Критическая ошибка", "error", "ERROR", message=str(e)))

        report.duration = time.time() - start
        return report

    # ============================================================
    # TARIFF CHECKS (read-only проверка текущего тарифа)
    # ============================================================

    def _run_tariff_checks(self, report, expected, cid, sid, rp_name, rp_id):
        """Проверки для теста тарифа."""

        # 3. Проверка имени тарифа
        target = report.target_name
        if target:
            report.checks.append(compare_string(target, rp_name, "Название тарифа"))

        # 4. RT Discounts — объёмы
        rt_data = self.client.get_rt_discounts(sid)
        report.raw_responses["rtDiscounts"] = rt_data
        if rt_data:
            totals, details = extract_volumes(rt_data)
            report.raw_responses["rtDiscounts_parsed"] = {"totals": totals, "details": details}

            c = compare_numeric(expected.get("minutesLimit"), totals["minutes"], "Минуты", "мин")
            c.api_endpoint = f"/PSAPI/.../subscribers/{sid}/rtDiscounts"
            c.category = "volumes"
            report.checks.append(c)

            c = compare_numeric(expected.get("smsLimit"), totals["sms"], "SMS", "SMS")
            c.category = "volumes"
            report.checks.append(c)

            # Интернет: поддержка ввода в ГБ
            internet_expected = expected.get("internetMb")
            if expected.get("internetGb"):
                internet_expected = float(expected["internetGb"]) * 1024
            c = compare_numeric(internet_expected, totals["mb"], "Интернет", "МБ")
            c.category = "volumes"
            report.checks.append(c)
        else:
            report.checks.append(CheckResult("RT Discounts", "volumes", "ERROR",
                                            message="Не удалось получить данные rtDiscounts"))

        # 5. Абонентская плата (Next Charges)
        if rp_id:
            nc_data = self.client.get_rateplan_next_charges(sid, rp_id)
            report.raw_responses["ratePlanNextCharges"] = nc_data
            if nc_data:
                charge_amount = extract_recurring_charge(nc_data)
                c = compare_numeric(expected.get("monthlyFee"), charge_amount, "Абон. плата (OAPI)", "сум")
                c.api_endpoint = f"/OAPI/.../nextCharges/ratePlans/{rp_id}"
                c.category = "fee"
                if charge_amount is None:
                    keys_info = ""
                    if isinstance(nc_data, dict):
                        keys_info = f"keys: {list(nc_data.keys())}"
                    elif isinstance(nc_data, list) and nc_data:
                        keys_info = f"list[{len(nc_data)}], first keys: {list(nc_data[0].keys()) if isinstance(nc_data[0], dict) else type(nc_data[0])}"
                    else:
                        keys_info = f"type: {type(nc_data).__name__}"
                    c.message = f"Не удалось извлечь amount из ответа ({keys_info})"
                    c.status = "FAIL"
                report.checks.append(c)
            else:
                report.checks.append(CheckResult("Абон. плата (OAPI)", "fee", "WARN",
                                                message="Не удалось получить nextCharges (пустой ответ или ошибка API)"))

        # 6. PSIX Next Fee — альтернативная проверка АП
        psix_fee = self.client.get_psix_next_fee(sid)
        report.raw_responses["psixNextFee"] = psix_fee if not isinstance(psix_fee, str) else {"raw": psix_fee}

        # 7. Активные пакеты
        active_packs = self.client.get_active_packs(sid)
        report.raw_responses["activePacks"] = active_packs
        pack_items = active_packs.get("items", []) if active_packs else []
        report.checks.append(CheckResult("Активные пакеты", "packs", "PASS" if active_packs else "WARN",
                                        actual=f"{len(pack_items)} шт",
                                        api_endpoint=f"/OAPI/v1/subscribers/{sid}/packs"))

        # 8. Доступные пакеты
        avail_packs = self.client.get_available_packs(sid)
        report.raw_responses["availablePacks"] = avail_packs
        avail_pack_items = avail_packs.get("items", []) if avail_packs else []

        c = compare_list_count(expected.get("availablePacksCount"), avail_pack_items, "Кол-во доступных пакетов")
        c.api_endpoint = f"/PSAPI/.../packs/availableForActivate"
        c.category = "packs"
        report.checks.append(c)

        if expected.get("availablePackNames"):
            c = compare_list_names(expected["availablePackNames"], avail_pack_items, "name", "Доступные пакеты (имена)")
            c.category = "packs"
            report.checks.append(c)

        # 9. Активные услуги
        active_svcs = self.client.get_active_services(sid)
        report.raw_responses["activeServices"] = active_svcs
        svc_items = active_svcs.get("items", []) if active_svcs else []
        report.checks.append(CheckResult("Активные услуги", "services", "PASS" if active_svcs else "WARN",
                                        actual=f"{len(svc_items)} шт",
                                        api_endpoint=f"/OAPI/v1/subscribers/{sid}/services"))

        # 10. Доступные услуги
        avail_svcs = self.client.get_available_services(sid)
        report.raw_responses["availableServices"] = avail_svcs
        avail_svc_items = avail_svcs.get("items", []) if avail_svcs else []

        c = compare_list_count(expected.get("availableServicesCount"), avail_svc_items, "Кол-во доступных услуг")
        c.category = "services"
        report.checks.append(c)

        if expected.get("availableServiceNames"):
            c = compare_list_names(expected["availableServiceNames"], avail_svc_items, "name", "Доступные услуги (имена)")
            c.category = "services"
            report.checks.append(c)

        # 11. Доступные тарифы для перехода
        avail_rps = self.client.get_available_rateplans(sid)
        report.raw_responses["availableRatePlans"] = avail_rps
        avail_rp_items = avail_rps.get("items", []) if avail_rps else []

        c = compare_list_count(expected.get("availableRatePlansCount"), avail_rp_items, "Кол-во доступных тарифов")
        c.category = "rateplans"
        report.checks.append(c)

        if expected.get("availableRatePlanNames"):
            c = compare_list_names(expected["availableRatePlanNames"], avail_rp_items, "name", "Доступные тарифы (имена)")
            c.category = "rateplans"
            report.checks.append(c)

        # 12. Жизненный цикл
        lc_data = self.client.get_lifecycle_actual(sid)
        report.raw_responses["lifecycleActual"] = lc_data
        if expected.get("lifecycleState") and lc_data:
            lc_state = ""
            if isinstance(lc_data, dict):
                lc_state = (lc_data.get("def") or lc_data.get("lcStateName")
                            or lc_data.get("stateName") or lc_data.get("name") or "")
                if not lc_state:
                    for key in ("items", "lcStates", "lcStatesList"):
                        sub = lc_data.get(key)
                        if isinstance(sub, list) and sub and isinstance(sub[0], dict):
                            lc_state = (sub[0].get("def") or sub[0].get("lcStateName")
                                        or sub[0].get("name") or "")
                            if lc_state:
                                break
            elif isinstance(lc_data, list) and lc_data and isinstance(lc_data[0], dict):
                lc_state = (lc_data[0].get("def") or lc_data[0].get("lcStateName")
                            or lc_data[0].get("name") or "")
            c = compare_string(expected["lifecycleState"], lc_state, "Состояние ЖЦ")
            c.category = "lifecycle"
            report.checks.append(c)

        # 13. Баланс (информационно)
        balance_data = self.client.get_available_balance(cid)
        report.raw_responses["availableBalance"] = balance_data
        if balance_data:
            bal_amount = balance_data.get("availableBalance", balance_data.get("availableAmount", "N/A"))
            report.checks.append(CheckResult("Баланс", "balance", "PASS",
                                            actual=f"{bal_amount} сум",
                                            api_endpoint=f"/PSAPI/.../availableBalance"))
            report.customer_info["balance"] = bal_amount

        # 14. Заказы на смену тарифа
        orders = self.client.get_rateplan_orders(sid)
        report.raw_responses["ratePlanOrders"] = orders

        # 15. PSIX Discounts info
        psix_disc = self.client.get_psix_discounts(sid, report.msisdn)
        report.raw_responses["psixDiscounts"] = psix_disc if not isinstance(psix_disc, str) else {"raw": psix_disc}

    # ============================================================
    # PACK CHECKS
    # ============================================================

    def _run_pack_checks(self, report, expected, cid, sid):
        """Проверки для теста пакета."""
        target = report.target_name

        # Активные пакеты — найти целевой
        active_packs = self.client.get_active_packs(sid)
        report.raw_responses["activePacks"] = active_packs
        pack_items = active_packs.get("items", []) if active_packs else []

        target_pack = None
        for p in pack_items:
            p_name = str(p.get("name", "")).lower()
            p_id = str(p.get("packId", ""))
            if target.lower() in p_name or target == p_id:
                target_pack = p
                break

        if target_pack:
            report.checks.append(CheckResult("Пакет найден", "pack", "PASS",
                                            expected=target,
                                            actual=target_pack.get("name"),
                                            api_endpoint=f"/OAPI/v1/subscribers/{sid}/packs"))
            pack_id = target_pack.get("packId") or target_pack.get("packInstanceId")

            # Next Charges для пакета
            if pack_id:
                nc = self.client.get_pack_next_charges(sid, pack_id)
                report.raw_responses["packNextCharges"] = nc
                if nc:
                    charge = extract_recurring_charge(nc)
                    c = compare_numeric(expected.get("monthlyFee"), charge, "Стоимость пакета", "сум")
                    c.category = "fee"
                    report.checks.append(c)
        else:
            report.checks.append(CheckResult("Пакет найден", "pack", "FAIL",
                                            expected=target, actual="Не найден",
                                            message=f"Пакет '{target}' не найден среди {len(pack_items)} активных"))

        # RT Discounts — объёмы
        rt_data = self.client.get_rt_discounts(sid)
        report.raw_responses["rtDiscounts"] = rt_data
        if rt_data:
            totals, details = extract_volumes(rt_data)
            report.raw_responses["rtDiscounts_parsed"] = {"totals": totals, "details": details}

            c = compare_numeric(expected.get("minutesLimit"), totals["minutes"], "Минуты (общие)", "мин")
            c.category = "volumes"
            report.checks.append(c)

            c = compare_numeric(expected.get("smsLimit"), totals["sms"], "SMS (общие)", "SMS")
            c.category = "volumes"
            report.checks.append(c)

            internet_expected = expected.get("internetMb")
            if expected.get("internetGb"):
                internet_expected = float(expected["internetGb"]) * 1024
            c = compare_numeric(internet_expected, totals["mb"], "Интернет (общие)", "МБ")
            c.category = "volumes"
            report.checks.append(c)

        # Баланс
        balance_data = self.client.get_available_balance(cid)
        report.raw_responses["availableBalance"] = balance_data
        if balance_data:
            bal = balance_data.get("availableBalance", balance_data.get("availableAmount", "N/A"))
            report.customer_info["balance"] = bal
            report.checks.append(CheckResult("Баланс", "balance", "PASS", actual=f"{bal} сум"))

    # ============================================================
    # SERVICE CHECKS
    # ============================================================

    def _run_service_checks(self, report, expected, cid, sid):
        """Проверки для теста услуги."""
        target = report.target_name

        # Активные услуги — найти целевую
        active_svcs = self.client.get_active_services(sid)
        report.raw_responses["activeServices"] = active_svcs
        svc_items = active_svcs.get("items", []) if active_svcs else []

        target_svc = None
        for s in svc_items:
            s_name = str(s.get("name", "")).lower()
            s_id = str(s.get("serviceId", ""))
            if target.lower() in s_name or target == s_id:
                target_svc = s
                break

        if target_svc:
            report.checks.append(CheckResult("Услуга найдена", "service", "PASS",
                                            expected=target, actual=target_svc.get("name")))
            svc_id = target_svc.get("serviceId")

            if svc_id:
                nc = self.client.get_service_next_charges(sid, svc_id)
                report.raw_responses["serviceNextCharges"] = nc
                if nc:
                    charge = extract_recurring_charge(nc)
                    c = compare_numeric(expected.get("monthlyFee"), charge, "Стоимость услуги", "сум")
                    c.category = "fee"
                    report.checks.append(c)
        else:
            report.checks.append(CheckResult("Услуга найдена", "service", "FAIL",
                                            expected=target, actual="Не найдена",
                                            message=f"Услуга '{target}' не найдена среди {len(svc_items)} активных"))

        # RT Discounts
        rt_data = self.client.get_rt_discounts(sid)
        report.raw_responses["rtDiscounts"] = rt_data
        if rt_data:
            totals, details = extract_volumes(rt_data)
            report.raw_responses["rtDiscounts_parsed"] = {"totals": totals, "details": details}

            c = compare_numeric(expected.get("minutesLimit"), totals["minutes"], "Минуты", "мин")
            c.category = "volumes"
            report.checks.append(c)

            c = compare_numeric(expected.get("smsLimit"), totals["sms"], "SMS", "SMS")
            c.category = "volumes"
            report.checks.append(c)

            internet_expected = expected.get("internetMb")
            if expected.get("internetGb"):
                internet_expected = float(expected["internetGb"]) * 1024
            c = compare_numeric(internet_expected, totals["mb"], "Интернет", "МБ")
            c.category = "volumes"
            report.checks.append(c)

        # Баланс
        balance_data = self.client.get_available_balance(cid)
        report.raw_responses["availableBalance"] = balance_data
        if balance_data:
            bal = balance_data.get("availableBalance", balance_data.get("availableAmount", "N/A"))
            report.customer_info["balance"] = bal
            report.checks.append(CheckResult("Баланс", "balance", "PASS", actual=f"{bal} сум"))

    # ============================================================
    # TARIFF CHANGE CHECKS (смена тарифа + верификация)
    # ============================================================

    def _run_tariff_change_checks(self, report, expected, cid, sid):
        """Проверки для теста смены тарифа: смена → ожидание → верификация."""

        # === 1. Баланс ДО смены ===
        balance_before_data = self.client.get_available_balance(cid)
        report.raw_responses["balanceBeforeChange"] = balance_before_data
        balance_before = None
        if balance_before_data:
            balance_before = balance_before_data.get("availableBalance",
                             balance_before_data.get("availableAmount"))
            report.customer_info["balanceBefore"] = balance_before

        # === 1b. rtDiscounts ДО смены (для определения productId нового тарифа) ===
        rt_before = self.client.get_rt_discounts(sid)
        report.raw_responses["rtDiscountsBefore"] = rt_before
        pids_before = set()
        if rt_before:
            for item in rt_before.get("items", []):
                pids_before.add(item.get("productId"))

        # === 2. Найти целевой тариф ===
        target_rp_id = expected.get("targetRatePlanId")
        target_rp_name = expected.get("targetRatePlanName") or report.target_name

        if not target_rp_id and not target_rp_name:
            report.checks.append(CheckResult(
                "Целевой тариф", "rateplan", "FAIL",
                message="Не указан ни ratePlanId, ни название тарифа"))
            return

        # Если нет ID — ищем в доступных тарифах
        if not target_rp_id:
            avail_rps = self.client.get_available_rateplans(sid)
            report.raw_responses["availableRatePlansBefore"] = avail_rps
            avail_items = avail_rps.get("items", []) if avail_rps else []

            for rp in avail_items:
                # items содержат вложенный ratePlan: {name, ratePlanId}
                rp_obj = rp.get("ratePlan") or {}
                rp_name_val = rp_obj.get("name") or rp.get("name", "")
                rp_id_val = rp_obj.get("ratePlanId") or rp.get("ratePlanId") or rp.get("id")
                if target_rp_name.lower() in str(rp_name_val).lower():
                    target_rp_id = rp_id_val
                    target_rp_name = rp_name_val
                    break

            if not target_rp_id:
                report.checks.append(CheckResult(
                    "Поиск тарифа", "rateplan", "FAIL",
                    expected=target_rp_name,
                    actual=f"Не найден среди {len(avail_items)} доступных"))
                return

        # === 3. Ожидаемая АП целевого тарифа через nextCharges ===
        target_nc = self.client.get_rateplan_next_charges(sid, target_rp_id)
        report.raw_responses["targetNextChargesBefore"] = target_nc
        target_fee = extract_recurring_charge(target_nc) if target_nc else None
        target_product_id = expected.get("targetProductId")

        # === 3b. ПРЕДПРОВЕРКА — возможна ли смена ===
        check_result = self.client.check_rateplan_change(sid, target_rp_id)
        report.raw_responses["ratePlanChangeCheck"] = check_result
        if isinstance(check_result, dict) and "error" in check_result:
            error_text = check_result.get("text", "")
            user_msg = self._parse_api_error(error_text)
            report.checks.append(CheckResult(
                "Предпроверка смены", "action", "FAIL",
                expected=f"{target_rp_name} (ID: {target_rp_id})",
                message=user_msg or f"HTTP {check_result['error']}: {error_text[:200]}"))
            return

        # === 4. СМЕНА ТАРИФА ===
        change_result = self.client.change_rateplan(sid, target_rp_id)
        report.raw_responses["ratePlanChangeResult"] = change_result

        if isinstance(change_result, dict) and "error" in change_result:
            error_code = change_result.get("error")
            error_text = change_result.get("text", "")
            user_msg = self._parse_api_error(error_text)
            report.checks.append(CheckResult(
                "Смена тарифа", "action", "FAIL",
                expected=f"{target_rp_name} (ID: {target_rp_id})",
                message=user_msg or f"HTTP {error_code}: {error_text[:200]}"))
            return

        # Извлечь orderId из ответа (HTTP 202 = асинхронная смена)
        order_id = None
        order_status_name = ""
        if isinstance(change_result, dict):
            order_id = change_result.get("ratePlanOrderId")
            order_status_name = (change_result.get("status") or {}).get("name", "")

        # === 5. Ожидание + опрос статуса заказа ===
        wait_sec = expected.get("waitTimeSeconds", 10)
        poll_interval = 3  # секунд между проверками
        max_polls = max(wait_sec // poll_interval, 3)

        final_order_status = order_status_name
        polls_done = 0
        if order_id:
            for i in range(max_polls):
                time.sleep(poll_interval)
                polls_done = i + 1
                # Используем общий список заказов (per-order endpoint возвращает null)
                all_orders = self.client.get_rateplan_orders(sid)
                report.raw_responses[f"orderPoll_{polls_done}"] = all_orders
                if all_orders and isinstance(all_orders, dict):
                    order_items = all_orders.get("items", [])
                    for o in order_items:
                        if str(o.get("ratePlanOrderId")) == str(order_id):
                            st = o.get("status") or {}
                            st_id = st.get("ratePlanOrderStatusId")
                            st_name = st.get("name", "")
                            final_order_status = st_name
                            if st_id != 5:  # не "В процессе"
                                break
                    else:
                        # orderId не найден в списке — проверим searchBase
                        pass
                # Также проверяем searchBase — тариф мог смениться
                search_check = self.client.search_customer(report.msisdn)
                if search_check and search_check.get("searchResults"):
                    check_rp = search_check["searchResults"][0].get("firstSubscriber", {}).get("ratePlan", {})
                    if check_rp.get("ratePlanId") == target_rp_id:
                        final_order_status = "completed"
                        break
            order_failed = ("отклон" in final_order_status.lower()
                            or "ошибк" in final_order_status.lower()
                            or "error" in final_order_status.lower())
            if order_failed:
                report.checks.append(CheckResult(
                    "Смена тарифа", "action", "FAIL",
                    expected="Выполнен",
                    actual=final_order_status,
                    message=f"Заказ {order_id}"))
        else:
            # Нет orderId — просто ждём фиксированное время
            time.sleep(wait_sec)

        # === 6. Проверка — тариф сменился ===
        search_after = self.client.search_customer(report.msisdn)
        report.raw_responses["searchBaseAfter"] = search_after

        new_rp_id = None
        new_rp_name = None
        if search_after and search_after.get("searchResults"):
            sr_after = search_after["searchResults"][0]
            new_rp = sr_after.get("firstSubscriber", {}).get("ratePlan", {})
            new_rp_id = new_rp.get("ratePlanId")
            new_rp_name = new_rp.get("name", "N/A")
            report.customer_info["ratePlanName"] = new_rp_name
            report.customer_info["ratePlanId"] = new_rp_id

        if new_rp_id != target_rp_id:
            report.checks.append(CheckResult(
                "Тариф сменился", "rateplan", "FAIL",
                expected=f"{target_rp_name} (ID: {target_rp_id})",
                actual=f"{new_rp_name} (ID: {new_rp_id})",
                message="Тариф не изменился после ожидания"))

        # === 7. АП нового тарифа — только для получения даты следующего списания ===
        if new_rp_id:
            nc_after = self.client.get_rateplan_next_charges(sid, new_rp_id)
            report.raw_responses["nextChargesAfter"] = nc_after

        # === 8. rtDiscounts — определить productId через diff before/after ===
        # subscriptionId из nextCharges НЕ равен productId в rtDiscounts!
        # Вместо этого сравниваем productIds ДО и ПОСЛЕ — новые pid = новый тариф.
        # rtDiscounts может обновляться с задержкой — делаем несколько попыток.
        rt_data = None
        rt_retries = 3
        for rt_attempt in range(rt_retries):
            if rt_attempt > 0:
                time.sleep(5)
            rt_data = self.client.get_rt_discounts(sid)
            if rt_data and not target_product_id:
                pids_after = set()
                for item in rt_data.get("items", []):
                    pids_after.add(item.get("productId"))
                new_pids = pids_after - pids_before
                # Исключаем productId=0 (безлимит/системные скидки)
                new_pids.discard(0)
                if new_pids:
                    # Если появился один новый productId — это новый тариф
                    # Если несколько — берём тот с наибольшим количеством items
                    best_pid = None
                    best_count = 0
                    for pid in new_pids:
                        cnt = sum(1 for i in rt_data.get("items", []) if i.get("productId") == pid)
                        if cnt > best_count:
                            best_count = cnt
                            best_pid = pid
                    target_product_id = best_pid
                    break
            elif target_product_id:
                break
        report.raw_responses["rtDiscountsAfter"] = rt_data

        if rt_data and target_product_id:
            totals, details, count = extract_volumes_by_product_id(rt_data, target_product_id)
            report.raw_responses["rtDiscounts_filtered"] = {
                "productId": target_product_id, "totals": totals,
                "details": details, "itemCount": count
            }
        elif rt_data:
            totals, details = extract_volumes(rt_data)
            report.raw_responses["rtDiscounts_parsed"] = {"totals": totals, "details": details}

        # === 9. Баланс ПОСЛЕ — проверка списания ===
        balance_after_data = self.client.get_available_balance(cid)
        report.raw_responses["balanceAfterChange"] = balance_after_data
        balance_after = None
        if balance_after_data:
            balance_after = balance_after_data.get("availableBalance",
                            balance_after_data.get("availableAmount"))
            report.customer_info["balanceAfter"] = balance_after
            report.customer_info["balance"] = balance_after

        if balance_before is not None and balance_after is not None:
            charge = balance_before - balance_after
            expected_charge = expected.get("expectedBalanceCharge")
            if expected_charge is None and target_fee is not None:
                expected_charge = target_fee
            # Проверяем списание — показываем только если не совпадает
            if expected_charge is not None:
                c = compare_numeric(expected_charge, charge, "Списание с баланса", "сум")
                c.category = "balance"
                c.message = f"До: {balance_before}, После: {balance_after}, Разница: {charge} сум"
                report.checks.append(c)
            else:
                report.checks.append(CheckResult(
                    "Списание с баланса", "balance", "INFO",
                    actual=f"{charge} сум",
                    message=f"До: {balance_before}, После: {balance_after}"))

        # === 10. Доступные тарифы ПОСЛЕ ===
        avail_rps_after = self.client.get_available_rateplans(sid)
        report.raw_responses["availableRatePlansAfter"] = avail_rps_after
        avail_rp_items_after = avail_rps_after.get("items", []) if avail_rps_after else []

        if expected.get("availableRatePlansCountAfter") is not None:
            c = compare_list_count(expected["availableRatePlansCountAfter"],
                                   avail_rp_items_after, "Доступных тарифов после смены")
            c.category = "rateplans"
            report.checks.append(c)

        # === 11. Доступные пакеты ПОСЛЕ ===
        avail_packs_after = self.client.get_available_packs(sid)
        report.raw_responses["availablePacksAfter"] = avail_packs_after
        avail_pack_items_after = avail_packs_after.get("items", []) if avail_packs_after else []

        if expected.get("availablePacksCountAfter") is not None:
            c = compare_list_count(expected["availablePacksCountAfter"],
                                   avail_pack_items_after, "Доступных пакетов после смены")
            c.category = "packs"
            report.checks.append(c)

        # === 12. Доступные услуги ПОСЛЕ ===
        avail_svcs_after = self.client.get_available_services(sid)
        report.raw_responses["availableServicesAfter"] = avail_svcs_after
        avail_svc_items_after = avail_svcs_after.get("items", []) if avail_svcs_after else []

        # === INFO: Скидки нового тарифа — детально (из rtDiscounts_filtered.details) ===
        unit_names_det = {1: "SMS", 7: "Минуты", 14: "Интернет", 0: "Деньги"}
        filtered_details = report.raw_responses.get("rtDiscounts_filtered", {}).get("details", [])
        if filtered_details:
            report.checks.append(CheckResult(
                "── Скидки нового тарифа ──", "section_hdr", "INFO",
                message=f"Всего позиций: {len(filtered_details)}"))
            for d in filtered_details:
                uid = d.get("measureUnitId", -1)
                u_label = unit_names_det.get(uid, f"unit_{uid}")
                name = d.get("discountDescription") or d.get("discountName") or ""
                pid_s = d.get("discountPlanId")
                if not name:
                    name = f"Plan {pid_s}" if pid_s else "—"
                pid_label = f" [ID:{pid_s}]" if pid_s else ""
                max_v = d.get("maxVolume", 0) or 0
                spent_v = d.get("spentVolume", 0) or 0
                remaining_v = max_v - spent_v
                end_d = (d.get("endDate", "") or "")[:10]
                msg = f"Использовано: {spent_v}" + (f", до: {end_d}" if end_d else "")
                report.checks.append(CheckResult(
                    f"{name}{pid_label}", "discount", "INFO",
                    expected=u_label,
                    actual=f"Выдано: {max_v}, Остаток: {remaining_v}",
                    message=msg))

        # === INFO: Следующая АП — дата из nextChargesAfter ===
        nc_after_data = report.raw_responses.get("nextChargesAfter")
        if nc_after_data:
            from sbms_checks import find_recurring_charges as _find_rc
            rc_list = _find_rc(nc_after_data)
            if rc_list and isinstance(rc_list, list) and rc_list:
                rc0 = rc_list[0]
                ncd = (rc0.get("nextChargeDate") or rc0.get("chargeDate")
                       or rc0.get("nextDate") or rc0.get("nextChargeOn") or "")
                fee_a = rc0.get("amount")
                if ncd or fee_a is not None:
                    report.checks.append(CheckResult(
                        "Следующее списание (дата)", "fee", "INFO",
                        expected=f"{fee_a} сум" if fee_a is not None else "—",
                        actual=str(ncd)[:10] if ncd else "Дата не указана",
                        message="Дата следующего списания АП"))

        # === INFO: Доступные тарифы после смены (список) ===
        report.checks.append(CheckResult(
            "── Доступные тарифы после смены ──", "section_hdr", "INFO",
            actual=f"Кол-во: {len(avail_rp_items_after)}"))
        for rp in avail_rp_items_after[:40]:
            rp_obj = rp.get("ratePlan") or {}
            rp_name_v = rp_obj.get("name") or rp.get("name", "N/A")
            rp_id_v   = rp_obj.get("ratePlanId") or rp.get("ratePlanId") or rp.get("id", "")
            report.checks.append(CheckResult(
                rp_name_v, "avail_tariffs", "INFO",
                actual=f"ID: {rp_id_v}"))

        # === INFO: Доступные пакеты после смены (список) ===
        report.checks.append(CheckResult(
            "── Доступные пакеты после смены ──", "section_hdr", "INFO",
            actual=f"Кол-во: {len(avail_pack_items_after)}"))
        for pk in avail_pack_items_after[:40]:
            pk_name = pk.get("name", "N/A")
            pk_id_v  = pk.get("packId", "")
            pk_fee_v = pk.get("fee") or pk.get("price") or ""
            fee_str  = f", {pk_fee_v} сум" if pk_fee_v else ""
            report.checks.append(CheckResult(
                pk_name, "avail_packs", "INFO",
                actual=f"ID: {pk_id_v}{fee_str}"))

        # === INFO: Доступные услуги после смены (список) ===
        report.checks.append(CheckResult(
            "── Доступные услуги после смены ──", "section_hdr", "INFO",
            actual=f"Кол-во: {len(avail_svc_items_after)}"))
        for sv in avail_svc_items_after[:40]:
            sv_name = sv.get("name", "N/A")
            sv_id_v  = sv.get("serviceId", "")
            sv_fee_v = sv.get("fee") or sv.get("price") or ""
            fee_str  = f", {sv_fee_v} сум" if sv_fee_v else ""
            report.checks.append(CheckResult(
                sv_name, "avail_services", "INFO",
                actual=f"ID: {sv_id_v}{fee_str}"))

    # ============================================================
    # PACK ACTIVATE CHECKS (подключение пакета + верификация)
    # ============================================================

    def _run_pack_activate_checks(self, report, expected, cid, sid):
        """Подключить пакет → подождать → проверить пакет активен + объёмы + списание."""

        target_pack_id   = expected.get("targetPackId")
        target_pack_name = expected.get("targetPackName") or report.target_name

        if not target_pack_id and not target_pack_name:
            report.checks.append(CheckResult(
                "Целевой пакет", "pack", "FAIL",
                message="Не указан ни packId, ни название пакета"))
            return

        unit_labels = {1: "SMS", 7: "Минуты", 14: "Интернет (МБ)", 0: "Деньги"}

        # === 1. Баланс ДО ===
        balance_before_data = self.client.get_available_balance(cid)
        report.raw_responses["balanceBeforeActivate"] = balance_before_data
        balance_before = None
        if balance_before_data:
            balance_before = balance_before_data.get("availableBalance",
                             balance_before_data.get("availableAmount"))
            report.customer_info["balanceBefore"] = balance_before

        # === 2. Найти пакет в доступных (если не задан ID) ===
        if not target_pack_id:
            avail_packs = self.client.get_available_packs(sid)
            report.raw_responses["availablePacksBefore"] = avail_packs
            avail_items = avail_packs.get("items", []) if avail_packs else []
            for pk in avail_items:
                pk_name = str(pk.get("name", "")).lower()
                if target_pack_name.lower() in pk_name:
                    target_pack_id   = pk.get("packId")
                    target_pack_name = pk.get("name", target_pack_name)
                    break
            if not target_pack_id:
                report.checks.append(CheckResult(
                    "Поиск пакета", "pack", "FAIL",
                    expected=target_pack_name,
                    actual=f"Не найден среди {len(avail_items)} доступных"))
                return

        # === 3. АП пакета (nextCharges) ===
        pack_nc = self.client.get_pack_next_charges(sid, target_pack_id)
        report.raw_responses["packNextChargesBefore"] = pack_nc
        pack_fee = extract_recurring_charge(pack_nc) if pack_nc else None

        # Дата следующего списания из nextCharges
        next_charge_date = None
        if pack_nc:
            from sbms_checks import find_recurring_charges as _find_rc
            rc_list = _find_rc(pack_nc)
            if rc_list:
                next_charge_date = (rc_list[0].get("nextChargeDate") or
                                    rc_list[0].get("chargeDate") or
                                    rc_list[0].get("date"))

        # === 4. RT Discounts ДО активации (снимок для сравнения после) ===
        rt_before = self.client.get_rt_discounts(sid)
        report.raw_responses["rtDiscountsBefore"] = rt_before

        # === 5. ПОДКЛЮЧЕНИЕ ПАКЕТА ===
        activate_result = self.client.activate_pack(sid, target_pack_id)
        report.raw_responses["packActivateResult"] = activate_result

        if isinstance(activate_result, dict) and activate_result.get("error"):
            report.checks.append(CheckResult(
                "Подключение пакета", "action", "FAIL",
                message=str(activate_result)))
            return

        # Извлечь productId из subscriberPackId (формат: "{packId}t{productId}")
        pack_product_id = None
        spid_raw = ""
        if isinstance(activate_result, dict):
            spid_raw = str(activate_result.get("subscriberPackId") or "")
        if "t" in spid_raw:
            try:
                pack_product_id = int(spid_raw.split("t", 1)[1])
            except (ValueError, IndexError):
                pass
        report.raw_responses["packProductId"] = pack_product_id

        # === 6. Ожидание ===
        wait_sec = expected.get("waitTimeSeconds", 0)
        if wait_sec:
            time.sleep(wait_sec)

        # === 7. Проверка — пакет появился в активных ===
        active_packs_after = self.client.get_active_packs(sid)
        report.raw_responses["activePacksAfter"] = active_packs_after
        pack_items_after = active_packs_after.get("items", []) if active_packs_after else []

        found_pack = None
        for pk in pack_items_after:
            pk_obj  = pk.get("pack") or pk
            if str(pk_obj.get("packId", "")) == str(target_pack_id) or \
               str(pk_obj.get("name", "")).lower() == target_pack_name.lower():
                found_pack = pk
                break

        if found_pack:
            fp_obj = found_pack.get("pack") or found_pack
            fp_name = fp_obj.get("name", target_pack_name)
            fp_status = (found_pack.get("status") or {}).get("name", "Подключён")
            fp_deact  = (found_pack.get("deactivationDate") or "")[:10]
            msg_parts = [f"Статус: {fp_status}"]
            if fp_deact:
                msg_parts.append(f"Действует до: {fp_deact}")
            report.checks.append(CheckResult(
                "Пакет подключён", "pack", "PASS",
                expected=f"{target_pack_name} (ID: {target_pack_id})",
                actual=fp_name,
                message=", ".join(msg_parts)))
        else:
            report.checks.append(CheckResult(
                "Пакет подключён", "pack", "FAIL",
                expected=f"{target_pack_name} (ID: {target_pack_id})",
                actual="Не найден в активных",
                message="Пакет не появился в активных подписках"))

        # === 8. Баланс ПОСЛЕ — списание ===
        balance_after_data = self.client.get_available_balance(cid)
        report.raw_responses["balanceAfterActivate"] = balance_after_data
        balance_after = None
        if balance_after_data:
            balance_after = balance_after_data.get("availableBalance",
                            balance_after_data.get("availableAmount"))
            report.customer_info["balanceAfter"] = balance_after
            report.customer_info["balance"] = balance_after

        if balance_before is not None and balance_after is not None:
            charge = round(balance_before - balance_after, 4)
            expected_charge = expected.get("expectedBalanceCharge")
            if expected_charge is None and pack_fee is not None:
                expected_charge = pack_fee
            if expected_charge is not None:
                c = compare_numeric(expected_charge, charge, "Списание с баланса", "сум")
                c.category = "balance"
                c.message = f"До: {balance_before}, После: {balance_after}, Разница: {charge} сум"
                report.checks.append(c)
            else:
                report.checks.append(CheckResult(
                    "Списание с баланса", "balance", "INFO",
                    actual=f"{charge} сум",
                    message=f"До: {balance_before}, После: {balance_after}"))

        # === 9. RT Discounts ПОСЛЕ — объёмы пакета (фильтр по productId + retry) ===
        # Стратегия: первичный метод — фильтр по productId из subscriberPackId
        # Запасной — diff суммарных объёмов (до/после)
        RETRY_DELAY = 3   # сек между попытками
        MAX_RETRIES = 3   # макс кол-во повторов

        rt_after = None
        pack_disc_items = []   # строки rtDiscounts, относящиеся к пакету

        for attempt in range(MAX_RETRIES + 1):
            rt_after = self.client.get_rt_discounts(sid)
            after_items_list = (rt_after.get("items") or []) if rt_after else []

            if pack_product_id:
                pack_disc_items = [i for i in after_items_list
                                   if i.get("productId") == pack_product_id]
                if pack_disc_items:
                    break   # нашли — больше ждать не надо
            else:
                break       # productId неизвестен — не повторяем

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

        report.raw_responses["rtDiscountsAfter"] = rt_after

        before_items_list = (rt_before.get("items") or []) if rt_before else []

        # --- Вычисляем объёмы ---
        if pack_disc_items:
            # Надёжный метод: суммируем строки с нашим productId
            vol_mb  = round(sum(i.get("maxVolume", 0) or 0 for i in pack_disc_items if i.get("measureUnitId") == 14), 4)
            vol_min = round(sum(i.get("maxVolume", 0) or 0 for i in pack_disc_items if i.get("measureUnitId") ==  7), 4)
            vol_sms = round(sum(i.get("maxVolume", 0) or 0 for i in pack_disc_items if i.get("measureUnitId") ==  1), 4)
            new_items = pack_disc_items
        else:
            # Запасной метод: diff суммарных объёмов до/после
            def _unit_total(items, uid):
                return sum(i.get("maxVolume", 0) or 0 for i in items if i.get("measureUnitId") == uid)

            vol_mb  = round(_unit_total(after_items_list, 14) - _unit_total(before_items_list, 14), 4)
            vol_min = round(_unit_total(after_items_list,  7) - _unit_total(before_items_list,  7), 4)
            vol_sms = round(_unit_total(after_items_list,  1) - _unit_total(before_items_list,  1), 4)

            # Новые строки (diff по callCreditId / discountPlanId)
            before_cids    = {i.get("callCreditId")   for i in before_items_list if i.get("callCreditId")}
            before_zero_pid= {i.get("discountPlanId") for i in before_items_list if not i.get("callCreditId")}
            before_vol_map = {i.get("callCreditId"): i.get("maxVolume", 0) or 0
                              for i in before_items_list if i.get("callCreditId")}
            new_items = []
            for item in after_items_list:
                cid = item.get("callCreditId")
                pid = item.get("discountPlanId")
                if cid:
                    if cid not in before_cids:
                        new_items.append(item)
                    elif (item.get("maxVolume") or 0) > before_vol_map.get(cid, 0):
                        delta = dict(item)
                        delta["maxVolume"] = round((item.get("maxVolume") or 0) - before_vol_map[cid], 4)
                        delta["_delta"] = True
                        new_items.append(delta)
                else:
                    if pid not in before_zero_pid:
                        new_items.append(item)

        # Дата окончания: из active pack (надёжнее), потом из rtDiscounts
        pack_end_date = None
        if found_pack:
            pack_end_date = (found_pack.get("deactivationDate") or "")[:10]
        if not pack_end_date and new_items:
            pack_end_date = (new_items[0].get("endDate") or "")[:10]

        delay_note = "Объёмы ещё не отражены в rtDiscounts — попробуйте увеличить время ожидания"


        # --- Интернет (МБ) ---
        exp_mb = expected.get("expectedMbLimit")
        if exp_mb is not None:
            if vol_mb == 0:
                report.checks.append(CheckResult(
                    "Интернет предоставлено", "volume", "WARN",
                    expected=f"{exp_mb} МБ", actual="0 МБ",
                    message=delay_note))
            else:
                c = compare_numeric(exp_mb, vol_mb, "Интернет предоставлено", "МБ")
                c.category = "volume"
                report.checks.append(c)
        elif vol_mb > 0:
            report.checks.append(CheckResult(
                "Интернет предоставлено", "volume", "INFO",
                actual=f"{vol_mb} МБ"))
        elif vol_mb == 0 and (expected.get("expectedMbLimit") is None):
            pass  # не показывать нулевой интернет

        # --- Минуты ---
        exp_min = expected.get("expectedMinutesLimit")
        if exp_min is not None:
            if vol_min == 0:
                report.checks.append(CheckResult(
                    "Минуты предоставлено", "volume", "WARN",
                    expected=f"{exp_min} мин", actual="0 мин",
                    message=delay_note))
            else:
                c = compare_numeric(exp_min, vol_min, "Минуты предоставлено", "мин")
                c.category = "volume"
                report.checks.append(c)
        elif vol_min > 0:
            report.checks.append(CheckResult(
                "Минуты предоставлено", "volume", "INFO",
                actual=f"{vol_min} мин"))

        # --- SMS ---
        exp_sms = expected.get("expectedSmsLimit")
        if exp_sms is not None:
            if vol_sms == 0:
                report.checks.append(CheckResult(
                    "SMS предоставлено", "volume", "WARN",
                    expected=f"{exp_sms} шт", actual="0 шт",
                    message=delay_note))
            else:
                c = compare_numeric(exp_sms, vol_sms, "SMS предоставлено", "шт")
                c.category = "volume"
                report.checks.append(c)
        elif vol_sms > 0:
            report.checks.append(CheckResult(
                "SMS предоставлено", "volume", "INFO",
                actual=f"{vol_sms} шт"))

        # Если ни один объём не изменился И пользователь не задал ни одного ожидаемого значения —
        # показываем один общий INFO-ряд (специфичные WARN-ряды уже добавлены выше, если ожидание задано)
        user_set_any_vol = (expected.get("expectedMbLimit") is not None or
                            expected.get("expectedMinutesLimit") is not None or
                            expected.get("expectedSmsLimit") is not None)
        if vol_mb == 0 and vol_min == 0 and vol_sms == 0 and after_items_list and not user_set_any_vol:
            report.checks.append(CheckResult(
                "Объёмы в rtDiscounts", "volume", "INFO",
                actual="Изменений не обнаружено",
                message=delay_note))

        # --- Дата окончания ---
        if pack_end_date:
            report.checks.append(CheckResult(
                "Действует до", "info", "INFO",
                actual=pack_end_date))

        # --- Следующее списание ---
        if pack_fee is not None:
            nc_msg = f"{pack_fee} сум"
            if next_charge_date:
                nc_msg += f", дата: {str(next_charge_date)[:10]}"
            report.checks.append(CheckResult(
                "Следующее списание", "info", "INFO",
                actual=nc_msg))

        # --- Детальные позиции новых/изменённых объёмов (INFO) ---
        if new_items:
            report.checks.append(CheckResult(
                "── Изменения в rtDiscounts ──", "section_hdr", "INFO",
                actual=f"Позиций: {len(new_items)}"))
            for item in new_items:
                uid   = item.get("measureUnitId", -1)
                label = unit_labels.get(uid, f"unit_{uid}")
                pid_s = item.get("discountPlanId")
                name  = (item.get("discountName")
                         or (pid_s and _get_disc_name(pid_s))
                         or (f"Plan {pid_s}" if pid_s else "—"))
                pid_l = f" [ID:{pid_s}]" if pid_s else ""
                max_v = item.get("maxVolume", 0) or 0
                spent = item.get("spentVolume", 0) or 0
                end_d = (item.get("endDate") or "")[:10]
                delta_pfx = "+Δ " if item.get("_delta") else ""
                msg   = f"Использовано: {spent}" + (f", до: {end_d}" if end_d else "")
                report.checks.append(CheckResult(
                    f"{delta_pfx}{name}{pid_l}", "discount", "INFO",
                    expected=label,
                    actual=f"Выдано: {max_v}, Остаток: {max_v - spent}",
                    message=msg))
