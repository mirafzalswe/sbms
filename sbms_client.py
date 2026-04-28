"""
SBMS API Client
================
HTTP-обёртка для всех вызовов SBMS API (OAPI, PSAPI, PSIX).
"""

import requests
import urllib3
import xml.etree.ElementTree as ET

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class SBMSClient:
    """Обёртка для всех SBMS API вызовов."""

    def __init__(self, base_url, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token = None

    def _get(self, path, params=None):
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = requests.get(url, params=params, timeout=self.timeout, verify=False)
        return resp

    def _post(self, path, params=None, json_body=None):
        url = f"{self.base_url}/{path.lstrip('/')}"
        resp = requests.post(url, params=params, json=json_body, timeout=self.timeout, verify=False)
        return resp

    def _safe_json(self, resp):
        """Parse JSON safely — returns None on empty body or decode error."""
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except Exception:
            return None

    # ======================== AUTH ========================

    def authenticate(self, login, password):
        resp = self._get("/OAPI/v1/tokens-stub/get", {"login": login, "password": password})
        if resp.status_code != 200:
            raise Exception(f"Auth failed: HTTP {resp.status_code} — {resp.text[:300]}")
        body = resp.text.strip()
        if not body:
            raise Exception("Auth failed: empty response body")
        # Попытка: ответ может быть JSON вместо XML
        if body.startswith("{") or body.startswith("["):
            try:
                data = resp.json()
                sid = data.get("SESSION_ID") or data.get("session_id") or data.get("sessionId") or data.get("token")
                if sid:
                    self.token = str(sid)
                    return self.token
                raise Exception(f"SESSION_ID not found in JSON auth response: {list(data.keys())}")
            except Exception as e:
                if "not found" in str(e):
                    raise
                raise Exception(f"Auth response is not valid JSON: {body[:300]}")
        # Стандартный путь: XML
        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            raise Exception(f"Auth response is not valid XML: {body[:300]}")
        el = root.find("SESSION_ID") or root.find(".//SESSION_ID")
        if el is not None and el.text:
            self.token = el.text
            return self.token
        # Fallback: поиск по всем тегам
        for child in root.iter():
            if child.text and len(child.text.strip()) > 10:
                self.token = child.text.strip()
                return self.token
        raise Exception(f"SESSION_ID not found in auth response. Root tag: <{root.tag}>, children: {[c.tag for c in root]}")

    # ======================== CUSTOMER ========================

    def search_customer(self, msisdn):
        resp = self._get("/OAPI/v1/customers/searchBase", {
            "identification": msisdn, "authToken": self.token
        })
        return self._safe_json(resp)

    def get_subscriber_info(self, subscriber_id):
        resp = self._get(f"/OAPI/v1/subscribers/{subscriber_id}", {
            "languageId": 1, "authToken": self.token
        })
        return self._safe_json(resp)

    # ======================== BALANCE ========================

    def get_available_balance(self, customer_id):
        resp = self._get(f"/PSAPI/v1/bis-base/customers/{customer_id}/availableBalance", {
            "authToken": self.token
        })
        return self._safe_json(resp)

    def get_rt_balance(self, customer_id):
        resp = self._get(f"/PSAPI/v1/bis-brt-balance/customers/{customer_id}/rtBalance", {
            "authToken": self.token
        })
        return self._safe_json(resp)

    # ======================== RT DISCOUNTS ========================

    def get_rt_discounts(self, subscriber_id):
        resp = self._get(f"/PSAPI/v1/bis-brt-balance/subscribers/{subscriber_id}/rtDiscounts", {
            "authToken": self.token, "customerDatabaseId": 902
        })
        return self._safe_json(resp)

    # ======================== RATE PLANS ========================

    def get_available_rateplans(self, subscriber_id):
        resp = self._post(
            f"/OAPI/v1/cbss/subscribers/{subscriber_id}/ratePlans/availableForChange/search",
            params={
                "languageId": 1, "limit": 500, "offset": 0,
                "returnCount": 1, "showFees": 1, "authToken": self.token
            }
        )
        return self._safe_json(resp)

    def get_rateplan_next_charges(self, subscriber_id, rateplan_id):
        resp = self._get(
            f"/OAPI/v1/subscribers/{subscriber_id}/subscriptions/nextCharges/ratePlans/{rateplan_id}",
            {"authToken": self.token}
        )
        return self._safe_json(resp)

    def get_rateplan_allowed_actions(self, subscriber_id):
        resp = self._get(f"/OAPI/v1/subscribers/{subscriber_id}/ratePlans/allowedActions", {
            "languageId": 1, "authToken": self.token
        })
        return self._safe_json(resp)

    def get_rateplan_orders(self, subscriber_id):
        resp = self._get(f"/OAPI/v1/subscribers/{subscriber_id}/ratePlans/orders", {
            "languageId": 1, "authToken": self.token
        })
        return self._safe_json(resp)

    def change_rateplan(self, subscriber_id, new_rateplan_id):
        """Сменить тариф абонента. API возвращает 202 + orderId (асинхронно)."""
        resp = self._post(
            f"/OAPI/v1/subscribers/{subscriber_id}/ratePlans/change",
            params={
                "languageId": 1,
                "newRatePlanId": new_rateplan_id,
                "authToken": self.token
            }
        )
        if resp.status_code in (200, 202):
            try:
                return resp.json()
            except Exception:
                return {"status": "ok", "text": resp.text}
        return {"error": resp.status_code, "text": resp.text}

    def get_rateplan_order_status(self, subscriber_id, order_id):
        """Проверить статус конкретного заказа на смену тарифа."""
        resp = self._get(f"/OAPI/v1/subscribers/{subscriber_id}/ratePlans/orders/{order_id}", {
            "languageId": 1, "authToken": self.token
        })
        if resp.status_code == 200:
            try:
                return resp.json()
            except Exception:
                return None
        return None

    def check_rateplan_change(self, subscriber_id, new_rateplan_id):
        """Предпроверка возможности смены тарифа."""
        resp = self._post(
            f"/OAPI/v1/subscribers/{subscriber_id}/ratePlans/change/check",
            params={
                "authToken": self.token,
                "isFullDiscountsInfo": "true",
                "languageId": 1,
                "newRatePlanId": new_rateplan_id
            }
        )
        if resp.status_code == 200:
            try:
                return resp.json()
            except Exception:
                return {"status": "ok", "text": resp.text}
        return {"error": resp.status_code, "text": resp.text}

    # ======================== PACKS ========================

    def get_active_packs(self, subscriber_id):
        resp = self._get(f"/OAPI/v1/subscribers/{subscriber_id}/packs", {
            "authToken": self.token
        })
        return self._safe_json(resp)

    def get_available_packs(self, subscriber_id):
        resp = self._get(f"/PSAPI/v1/bis-base/subscribers/{subscriber_id}/packs/availableForActivate", {
            "authToken": self.token, "limit": 500, "unlimited": 1, "offset": 0
        })
        return self._safe_json(resp)

    def activate_pack(self, subscriber_id, pack_id):
        """Активация пакета для абонента."""
        resp = self._post(
            f"/OAPI/v1/subscribers/{subscriber_id}/packs/activate",
            params={
                "authToken": self.token,
                "packId": pack_id
            },
            json_body={"actionParameters": {}}
        )
        return self._safe_json(resp) or {"status_code": resp.status_code, "text": resp.text}

    def deactivate_pack(self, subscriber_id, pack_instance_id):
        """Деактивация пакета по packInstanceId."""
        resp = self._post(
            f"/OAPI/v1/subscribers/{subscriber_id}/packs/{pack_instance_id}/deactivate",
            params={"authToken": self.token},
            json_body={"actionParameters": {}}
        )
        return self._safe_json(resp) or {"status_code": resp.status_code, "text": resp.text}

    def get_pack_next_charges(self, subscriber_id, pack_id):
        resp = self._get(
            f"/OAPI/v1/subscribers/{subscriber_id}/subscriptions/nextCharges/packs/{pack_id}",
            {"authToken": self.token}
        )
        return self._safe_json(resp)

    # ======================== SERVICES ========================

    def get_active_services(self, subscriber_id):
        resp = self._get(f"/OAPI/v1/subscribers/{subscriber_id}/services", {
            "authToken": self.token
        })
        return self._safe_json(resp)

    def get_available_services(self, subscriber_id):
        resp = self._get(f"/PSAPI/v1/bis-base/subscribers/{subscriber_id}/services/availableForActivate", {
            "authToken": self.token
        })
        return self._safe_json(resp)

    def activate_service(self, subscriber_id, service_id):
        """Активация услуги для абонента."""
        resp = self._post(
            f"/OAPI/v1/subscribers/{subscriber_id}/services/activate",
            params={
                "authToken": self.token,
                "serviceId": service_id
            }
        )
        return self._safe_json(resp) or {"status_code": resp.status_code, "text": resp.text}

    def deactivate_service(self, subscriber_id, service_instance_id):
        """Деактивация услуги по serviceInstanceId."""
        resp = self._post(
            f"/OAPI/v1/subscribers/{subscriber_id}/services/{service_instance_id}/deactivate",
            params={"authToken": self.token}
        )
        return self._safe_json(resp) or {"status_code": resp.status_code, "text": resp.text}

    def get_service_next_charges(self, subscriber_id, service_id):
        resp = self._get(
            f"/OAPI/v1/subscribers/{subscriber_id}/subscriptions/nextCharges/services/{service_id}",
            {"authToken": self.token}
        )
        return self._safe_json(resp)

    # ======================== LIFECYCLE ========================

    def get_lifecycle_actual(self, subscriber_id):
        resp = self._post(
            f"/PSAPI/internal/v1/licy-base-private/subslcstates/actual/{subscriber_id}",
            params={"languageId": 1, "authToken": self.token},
            json_body={}
        )
        if resp.status_code == 200:
            try:
                return resp.json()
            except Exception:
                return None
        return None

    def get_lifecycle_info(self, customer_id):
        resp = self._post(
            f"/PSAPI/internal/v1/licy-base-private/customers/{customer_id}/subscribers/lifeCycleInfo/search",
            params={"languageId": 1, "limit": 10, "offset": 0, "returnCount": 1, "authToken": self.token},
            json_body={"returnLCStatesList": True}
        )
        if resp.status_code == 200:
            try:
                return resp.json()
            except Exception:
                return None
        return None

    # ======================== HISTORY / ORDERS / PAYMENTS ========================

    def get_combined_history(self, customer_id, subscriber_id,
                             start_date, end_date, limit=10000, offset=0):
        """История активаций/деактиваций пакетов и услуг через PSIX combhist.

        Endpoint возвращает XML (SELFCARE wrapper). Парсим ROW-ы из тега
        <COMBINED_HISTORY_GET_SERVICES_PACKS> и возвращаем список словарей.

        start_date / end_date — формат "DD.MM.YYYY HH:MM:SS".
        """
        resp = self._get("/PSIX/combhist/COMBINED_HISTORY_GET_SERVICES_PACKS", {
            "SESSION_ID": self.token,
            "customerId": customer_id,
            "subscriberId": subscriber_id,
            "startDateFilter": start_date,
            "endDateFilter": end_date,
            "limit": limit,
            "offset": offset,
        })
        if resp.status_code != 200 or not resp.text:
            return None
        try:
            root = ET.fromstring(resp.text)
        except ET.ParseError:
            return None
        block = root.find("COMBINED_HISTORY_GET_SERVICES_PACKS")
        if block is None:
            block = root.find(".//COMBINED_HISTORY_GET_SERVICES_PACKS")
        if block is None:
            return {"items": []}
        rows = []
        for row in block.findall("ROW"):
            item = {}
            for f in row:
                item[f.tag] = (f.text or "").strip() if f.text else None
            rows.append(item)
        return {"items": rows, "count": len(rows)}

    def get_lifecycle_history(self, subscriber_id):
        """История смен жизненного цикла (блокировки/активации/закрытия)."""
        resp = self._post(
            "/PSAPI/internal/v1/licy-base-private/subslcstates/history/search",
            params={"languageId": 1, "authToken": self.token},
            json_body={"subs": subscriber_id},
        )
        return self._safe_json(resp)

    def get_balance_events_days(self, customer_id, date_from, date_to=None,
                                timeout_override=120):
        """Дневная разбивка движений по балансу: пополнения, списания АП,
        разовые начисления, корректировки. Группировка по eventType.

        date_from / date_to — формат "YYYY-MM-DDThh:mm:ss".
        Endpoint тяжёлый — даёт ~5-7 КБ на день, для длинных периодов нужен
        увеличенный таймаут.
        """
        params = {
            "languageId": 1,
            "authToken": self.token,
            "dateFrom": date_from,
        }
        if date_to:
            params["dateTo"] = date_to
        url = f"{self.base_url}/OAPI/v1/sbms/customers/{customer_id}/balances/events/days"
        resp = requests.get(url, params=params,
                            timeout=timeout_override or self.timeout,
                            verify=False)
        return self._safe_json(resp)

    def get_payments(self, customer_id, date_from=None, date_to=None,
                     limit=100, offset=0):
        """История платежей абонента, сортировка по дате DESC."""
        params = {
            "languageId": 1,
            "limit": limit,
            "offset": offset,
            "sort": "-paymentDate",
            "authToken": self.token,
        }
        if date_from:
            params["paymentDate.dateFrom"] = date_from
        if date_to:
            params["paymentDate.dateTo"] = date_to
        resp = self._get(f"/PSAPI/v1/fim/customers/{customer_id}/payments", params)
        return self._safe_json(resp)

    def search_rateplan_orders(self, subscriber_id, limit=100, offset=0,
                               status_ids=None):
        """Расширенный список заказов на смену тарифа (включая историю)."""
        params = {
            "languageId": 1,
            "authToken": self.token,
            "limit": limit,
            "offset": offset,
            "isFullHistoryAndStorno": "true",
        }
        if status_ids:
            params["ratePlanOrderStatusIds"] = status_ids
        resp = self._get(
            f"/OAPI/v1/subscribers/{subscriber_id}/ratePlans/orders",
            params,
        )
        return self._safe_json(resp)

    # ======================== PSIX ========================

    def get_psix_next_fee(self, subscriber_id):
        resp = self._get("/PSIX/scli/UCELL_NEXT_TIME_FEE", {
            "SESSION_ID": self.token, "P_SUBS_ID": subscriber_id
        })
        if resp.status_code == 200:
            try:
                return resp.json()
            except Exception:
                return {"raw": resp.text}
        return {"raw": resp.text}

    def get_psix_discounts(self, subscriber_id, msisdn):
        resp = self._get("/PSIX/scli/UCELL_DISCOUNTS_INFO", {
            "SESSION_ID": self.token, "P_SUBS_ID": subscriber_id,
            "SUBSCRIBER_MSISDN": msisdn
        })
        if resp.status_code == 200:
            try:
                return resp.json()
            except Exception:
                return {"raw": resp.text}
        return {"raw": resp.text}
