"""
SBMS API Client
================
HTTP-обёртка для всех вызовов SBMS API (OAPI, PSAPI, PSIX).
"""

import re
import requests
import urllib3
import xml.etree.ElementTree as ET

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


# =================================================================
# Безопасный debug-snippet ответов PSIX/SELFCARE
# =================================================================
# Контекст: PSIX (STRAFF_*, /scli/*) всегда оборачивает payload в
# <SELFCARE>...</SELFCARE>, в которой передаются авторизационные поля
# текущего сервера (SESSION_ID, LOGIN, ACCESS_LEVEL_ID и пр.).
# Этот wrapper НИКОГДА не должен попадать в UI-debug — иначе чужая
# сессия утекает наблюдателю.
#
# Защита построена двумя слоями (defense in depth):
#   1) allow-list: парсим XML и берём ТОЛЬКО полезные диагностические
#      узлы — ROW, ERR_CODE, ERR_TEXT, STATUS, MESSAGE. Всё остальное
#      (включая ещё не известные нам auth-поля) выбрасывается целиком.
#   2) deny-list: если XML невалиден или пуст — режем по списку
#      чувствительных тегов через regex. Список расширен с запасом.

# Чувствительные теги — для deny-list fallback.
# Список намеренно с запасом: лучше замаскировать лишнее, чем пропустить.
_SENSITIVE_SNIPPET_TAGS = (
    "SESSION_ID", "LOGIN", "USER_LOGIN",
    "ACCESS_LEVEL_ID", "ACCESS_LEVEL",
    "TOKEN", "AUTH_TOKEN", "API_KEY",
    "PASSWORD", "PWD", "SECRET",
    "TICKET", "COOKIE", "CSRF",
    "HAS_GET_USER_ATTRIBUTES", "CMS_ACRS_USER_ID",
)
_SENSITIVE_OPEN_RE = re.compile(
    r"<(" + "|".join(_SENSITIVE_SNIPPET_TAGS) + r")(\s[^>]*)?>",
    re.IGNORECASE,
)

# Полезные узлы, которые ИМЕЕТ смысл показывать в debug — allow-list.
_ALLOWED_DIAGNOSTIC_TAGS = (
    "ROW", "STATUS", "ERR_CODE", "ERR_TEXT",
    "MESSAGE", "MSG", "RESULT", "DATASET",
)


def _redact_snippet(text):
    """Deny-list: маскирует содержимое чувствительных тегов.

    Используется как fallback, когда XML-парсинг не сработал
    (битая разметка, обрезанный хвост и т.п.). Безопасно работает
    с обрезанным текстом (без закрывающего тега).
    """
    if not text:
        return text
    out = []
    pos = 0
    for m in _SENSITIVE_OPEN_RE.finditer(text):
        tag = m.group(1)
        out.append(text[pos:m.start()])
        out.append(f"<{tag}>")
        close_re = re.compile(r"</" + re.escape(tag) + r"\s*>", re.IGNORECASE)
        cm = close_re.search(text, m.end())
        if cm:
            out.append(f"***</{tag}>")
            pos = cm.end()
        else:
            # Хвост обрезан — режем всё после открывающего тега.
            out.append("***")
            pos = len(text)
            break
    out.append(text[pos:])
    return "".join(out)


def _psix_safe_snippet(text, max_len=400):
    """Безопасный debug-preview ответа PSIX/SELFCARE.

    Стратегия: если XML парсится — отбрасываем SELFCARE-обёртку и
    отдаём содержимое первого не-мета блока (это сам dataset, например
    <STRAFF_CALLS>...</STRAFF_CALLS>). Auth-поля (SESSION_ID/LOGIN/...)
    лежат как сиблинги обёртки, в dataset их нет. Поверх — deny-list
    redact на случай, если процедура зачем-то вернула вложенный SESSION_ID.
    Если XML битый — fallback на deny-list redact исходного текста.
    """
    if not text:
        return text or ""

    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return _redact_snippet(text)[:max_len]

    # Полный список SELFCARE-меты — сюда не должны попадать в snippet.
    META = {
        "HOSTNAME", "CHANNEL", "SESSION_ID", "LOGIN", "USER_LOGIN",
        "ACCESS_LEVEL_ID", "ACCESS_LEVEL", "LANG_ID",
        "HAS_GET_USER_ATTRIBUTES", "CMS_ACRS_USER_ID",
        "SERVER_TIME", "USER_INFO",
        "TOKEN", "AUTH_TOKEN", "API_KEY",
        "PASSWORD", "PWD", "SECRET", "TICKET", "COOKIE", "CSRF",
    }

    # Собираем все не-мета прямые потомки корня. Бывает два случая:
    #   1) Один dataset-блок c вложенными ROW (<STRAFF_CALLS>, <UCL_GET_CHARGE> и т.п.)
    #   2) Серия скалярных полей (STATUS / ERR_CODE / ERR_TEXT) — ответ-ошибка
    payload_children = [
        c for c in root if (c.tag or "").upper() not in META
    ]

    if payload_children:
        parts = []
        for c in payload_children:
            try:
                parts.append(ET.tostring(c, encoding="unicode"))
            except Exception:
                txt = (c.text or "").strip()
                parts.append(f"<{c.tag}>{txt}</{c.tag}>" if txt else f"<{c.tag}/>")
            # Если уже один тяжёлый dataset — хватит, остальное будет шумом.
            if len("".join(parts)) >= max_len:
                break
        joined = "".join(parts)
        # Defense-in-depth: на случай вложенных секретов в dataset.
        return _redact_snippet(joined)[:max_len]

    # Не-мета детей нет — голая SELFCARE-обёртка. Структурный обзор.
    names = []
    for c in list(root)[:8]:
        t = (c.tag or "").upper()
        names.append("***" if t in META else c.tag)
    return f"<{root.tag}>[{', '.join(names)}]</{root.tag}>"[:max_len]


class SBMSClient:
    """Обёртка для всех SBMS API вызовов."""

    def __init__(self, base_url, timeout=30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.token = None
        # Часть PSIX-grid процедур (например, STRAFF_CALLS_R) требует HTTP
        # Basic Authorization помимо SESSION_ID. Сохраняем учётку из последнего
        # authenticate() для повторного использования.
        self._login = None
        self._password = None
        # Persistent requests.Session() для PSIX-вызовов:
        # SBMS PSIX — это легаси-PHP с PHPSESSID-cookies + referer-check.
        # Без cookies сервер возвращает 404 на корректные процедуры.
        # Session заводится lazy (при первом _psix_call) и пере-инициализируется
        # при инвалидации (401/403/404 после warm-up).
        self._psix_session = None
        self._psix_warm = False

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

    # ======================== PSIX session management ========================

    def _psix_default_headers(self):
        """Headers, которые SBMS UI шлёт на /PSIX/* (см. curl-дамп с прода).

        - `Referer` — некоторые SBMS-инсталляции отвергают PSIX-вызовы,
          если referer не указывает на shell.html. Это не CSRF, а origin-check.
        - `User-Agent` — иногда WAF блокирует `python-requests/*`.
        - `pstxid` — уникальный transaction ID; на quota-процедуру не строго
          обязателен, но добавляем для консистентности и трассировки.
        - `Accept` — без него часть PSIX-сборок отдаёт XML вместо JSON.
        """
        import uuid
        return {
            "Accept":          "application/json, text/plain, */*",
            "Accept-Language": "ru,en;q=0.9",
            "Referer":         f"{self.base_url}/ps/sbms/shell.html",
            "Origin":          self.base_url,
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/147.0.0.0 Safari/537.36"
            ),
            "pstxid":          str(uuid.uuid4()),
            "X-Requested-With": "XMLHttpRequest",
        }

    def _psix_get_session(self):
        """Возвращает persistent requests.Session() для PSIX, прогревая её
        при первом обращении.

        Warm-up = GET `/ps/sbms/shell.html` (страница, на которую ссылается
        Referer). Этого достаточно, чтобы SBMS установил `PHPSESSID` cookie,
        привязанный к нашему IP/UA. Дальше каждый PSIX-вызов идёт через
        ту же сессию, и сервер уже видит контекст.
        """
        if self._psix_session is None:
            sess = requests.Session()
            sess.verify = False
            if self._login and self._password:
                sess.auth = (self._login, self._password)
            self._psix_session = sess
            self._psix_warm = False
        if not self._psix_warm:
            try:
                warm_url = f"{self.base_url}/ps/sbms/shell.html"
                self._psix_session.get(
                    warm_url,
                    headers={
                        "Accept": "text/html,application/xhtml+xml",
                        "User-Agent": self._psix_default_headers()["User-Agent"],
                    },
                    timeout=self.timeout,
                )
                self._psix_warm = True
            except Exception:
                # Warm-up best-effort: даже если упал, идём дальше — на проде
                # cookies могут уже быть установлены другим способом.
                self._psix_warm = True
        return self._psix_session

    def _psix_invalidate_session(self):
        """Сбросить PSIX-сессию (и cookies/warm-up). Вызывается на 401/403/404
        от живого endpoint'а, чтобы дать одну попытку с чистой сессией."""
        try:
            if self._psix_session is not None:
                self._psix_session.close()
        except Exception:
            pass
        self._psix_session = None
        self._psix_warm = False

    def _psix_call(self, path, params=None, allow_retry=True):
        """Единая точка входа для PSIX-вызовов с правильным контекстом.

        Поведение:
          1. Берём persistent Session с уже накопленным PHPSESSID-cookie
             и Basic-auth (если есть login/password).
          2. Шлём GET с полным набором headers (Referer, UA, Accept, pstxid).
          3. На 401/403/404 при первой попытке — сбрасываем сессию и
             повторяем один раз (часто помогает: сервер просрочил
             PHPSESSID, переотдаёт после warm-up).

        Возвращает (response, attempts:list[dict]). Каждый attempt:
          { url, status, retry?:int, error?:str, snippet?:str }
        """
        sess = self._psix_get_session()
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = self._psix_default_headers()
        attempts = []

        def _do(retry_idx):
            try:
                r = sess.get(url, params=params, headers=headers, timeout=self.timeout)
                a = {"url": path, "status": r.status_code, "retry": retry_idx}
                return r, a
            except requests.RequestException as e:
                return None, {"url": path, "error": str(e)[:200], "retry": retry_idx}

        resp, attempt = _do(0)
        attempts.append(attempt)
        if resp is not None and resp.status_code == 200:
            return resp, attempts

        # Вторая попытка только на «вероятно auth/cookie» ошибках, не на 5xx.
        if allow_retry and resp is not None and resp.status_code in (401, 403, 404):
            self._psix_invalidate_session()
            sess = self._psix_get_session()
            headers = self._psix_default_headers()
            resp2, attempt2 = _do(1)
            attempts.append(attempt2)
            if resp2 is not None:
                resp = resp2
        return resp, attempts

    # ======================== AUTH ========================

    def authenticate(self, login, password):
        # Сохраняем для PSIX-grid процедур, требующих HTTP Basic
        self._login = login
        self._password = password
        # Новый логин = новая сессия. Сбрасываем PSIX-cookies, чтобы
        # следующий PSIX-вызов сделал warm-up с актуальной учёткой.
        self._psix_invalidate_session()
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
        # SBMS отдаёт ошибки в виде <ERROR><ERROR_ID>...</ERROR_ID><ERROR_MESSAGE>...</ERROR_MESSAGE></ERROR>
        # Проверяем это ДО любого фолбэка, иначе текст ошибки попадёт в self.token.
        err_msg_el = root.find(".//ERROR_MESSAGE")
        if err_msg_el is not None and err_msg_el.text:
            err_id_el = root.find(".//ERROR_ID")
            err_id = err_id_el.text.strip() if (err_id_el is not None and err_id_el.text) else "?"
            raise Exception(f"Auth failed (ERROR_ID={err_id}): {err_msg_el.text.strip()}")
        el = root.find("SESSION_ID") or root.find(".//SESSION_ID")
        if el is not None and el.text:
            self.token = el.text
            return self.token
        raise Exception(f"SESSION_ID not found in auth response. Root tag: <{root.tag}>, body: {body[:300]}")

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

    def get_subscriber_identification(self, subscriber_id):
        """Identification + mainSIMCard (откуда берётся IMSI).

        Зеркалит запрос, который SBMS UI делает перед `UCELL_GET_SUBSCRIBER_ALL_QUOTA`:
          GET /OAPI/v1/subscribers/{sid}?fields=identification,typeSpecificAttributes(mainSIMCard)

        Возвращает dict с полями `identification` (MSISDN) и
        `typeSpecificAttributes.mainSIMCard.imsi`.
        """
        resp = self._get(f"/OAPI/v1/subscribers/{subscriber_id}", {
            "fields": "identification,typeSpecificAttributes(mainSIMCard)",
            "languageId": 1,
            "authToken": self.token,
        })
        return self._safe_json(resp)

    # ======================== BALANCE ========================

    def get_available_balance(self, customer_id):
        resp = self._get(f"/PSAPI/v1/bis-base/customers/{customer_id}/availableBalance", {
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
                "returnCount": 1, "showFees": 1,
                # Архивные тарифы недоступны для перехода — иначе матрица переходов
                # подсвечивает их как «+», что не соответствует бизнес-логике.
                "showArchiveRatePlans": "false",
                "authToken": self.token,
            }
        )
        return self._safe_json(resp)

    def get_available_rateplans_with_fees(self, subscriber_id):
        """GET /availableForChange (без /search) — этот endpoint отдаёт ratePlanCost
        с activationCost и subscriptionFeeDetail.amount. POST /search их НЕ возвращает.
        """
        resp = self._get(
            f"/OAPI/v1/cbss/subscribers/{subscriber_id}/ratePlans/availableForChange",
            {
                "languageId": 1, "limit": 500, "offset": 0,
                "otherServiceTypeRatePlansVisibility": "NOT_VISIBLE",
                "returnCount": "true", "showArchiveRatePlans": "false",
                "showFees": "true", "showFutureRatePlans": "true",
                "authToken": self.token,
            }
        )
        return self._safe_json(resp)

    def get_rateplan_next_charges(self, subscriber_id, rateplan_id):
        resp = self._get(
            f"/OAPI/v1/subscribers/{subscriber_id}/subscriptions/nextCharges/ratePlans/{rateplan_id}",
            {"authToken": self.token}
        )
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
            "authToken": self.token,
            "languageId": 1,
            "limit": 0,
            "showFees": "true",
            "unlimited": 1,
        })
        return self._safe_json(resp)

    def search_subscriber_packs(self, subscriber_id, limit=2000, name_like=None):
        """POST /OAPI/v1/subscribers/{sid}/packs/search?onlyRealPackFlag=0

        ВНИМАНИЕ: это POST с JSON-body фильтров (а НЕ GET). GET даёт HTTP 404.
        В body — фильтры по имени (LIKE с %), статусам, владельцам и датам.

        В отличие от /packs (только дефолтные) и /availableForActivate
        (только то, что можно активировать), этот endpoint возвращает ВСЕ
        подключённые на абоненте пакеты с учётом фильтров — включая
        системные/автоматические пакеты, «приехавшие» с тарифом.
        Для проверки «продукт X входит в состав тарифа Y» — нужен именно он.
        """
        fields = ("subscriberPackId,pack,status,activationDate,deactivationDate,"
                  "activateUser,changeUser,accountType,owner,updateComment,"
                  "deleteComment,comment,currentSubscriptionFeeDiscount,order,category")
        # Полный набор статусов — без фильтрации мы хотим увидеть всё, что
        # реально привязано к абоненту (Подключен/В процессе/Отключён/...).
        body = {
            "name": name_like,
            "subscriptionFeeDiscountName": None,
            "packStatusIds": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
            "packOwnerIds": [],
            "accountTypeIds": [],
            "activationDate":   {"dateFrom": None, "dateTo": None},
            "deactivationDate": {"dateFrom": None, "dateTo": None},
        }
        resp = self._post(
            f"/OAPI/v1/subscribers/{subscriber_id}/packs/search",
            params={
                "fields": fields, "languageId": 1,
                "limit": limit, "offset": 0,
                "onlyRealPackFlag": 0,
                "authToken": self.token,
            },
            json_body=body,
        )
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
            "authToken": self.token,
            "languageId": 1,
            "limit": 0,
            "showFees": "true",
            "unlimited": 1,
        })
        return self._safe_json(resp)

    def search_subscriber_services(self, subscriber_id, limit=2000, name_like=None):
        """POST /OAPI/v1/subscribers/{sid}/services/search?onlyRealServiceFlag=0

        Зеркало search_subscriber_packs для услуг — POST с body-фильтрами,
        возвращает все подключённые на абоненте услуги (включая автоматические).
        """
        fields = ("subscriberServiceId,service,status,activationDate,deactivationDate,"
                  "activateUser,changeUser,accountType,owner,updateComment,"
                  "deleteComment,comment,currentSubscriptionFeeDiscount,order,category")
        body = {
            "name": name_like,
            "subscriptionFeeDiscountName": None,
            "serviceStatusIds": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12],
            "serviceOwnerIds": [],
            "accountTypeIds": [],
            "activationDate":   {"dateFrom": None, "dateTo": None},
            "deactivationDate": {"dateFrom": None, "dateTo": None},
        }
        resp = self._post(
            f"/OAPI/v1/subscribers/{subscriber_id}/services/search",
            params={
                "fields": fields, "languageId": 1,
                "limit": limit, "offset": 0,
                "onlyRealServiceFlag": 0,
                "authToken": self.token,
            },
            json_body=body,
        )
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

    def get_subscriber_objects(self, subscriber_id):
        """Список объектов абонента (тариф/пакеты/услуги) с датами зарядов.

        Возвращает items вида:
          { objectId, name, objectType:{objectTypeId,name},
            startDate, endDate, lastPayDate, chargeStartDate, chargeEndDate }
        chargeEndDate — дата следующего списания.
        """
        params = {
            "languageId": 1,
            "limit": 10000,
            "offset": 0,
            "returnCount": 1,
            "authToken": self.token,
        }
        body_variants = [
            {"subs": subscriber_id},
            {"subsId": subscriber_id},
            {"subscriberId": subscriber_id},
        ]
        last_resp = None
        for body in body_variants:
            resp = self._post(
                "/PSAPI/internal/v1/esm-private/objects/search",
                params=params,
                json_body=body,
            )
            last_resp = resp
            if resp.status_code == 200:
                data = self._safe_json(resp)
                if isinstance(data, dict) and (data.get("items") or data.get("listInfo")):
                    return data
                if isinstance(data, dict):
                    return data
        if last_resp is not None and last_resp.status_code != 200:
            print(f"[WARN] subscriber_objects HTTP {last_resp.status_code}: "
                  f"{last_resp.text[:200] if last_resp.text else ''}")
        return self._safe_json(last_resp) if last_resp is not None else None

    def get_lifecycle_history(self, subscriber_id):
        """История смен жизненного цикла (блокировки/активации/закрытия).

        Возвращает items со структурой { lcState:{def}, lcStateDate, endDate, ... }.
        Текущее состояние — запись с открытым endDate (~2999).
        """
        params = {
            "languageId": 1,
            "limit": 10000,
            "offset": 0,
            "returnCount": 1,
            "authToken": self.token,
        }
        # Пробуем основной body. Если 4xx — повторяем с альтернативным именем поля.
        body_variants = [{"subs": subscriber_id}, {"subsId": subscriber_id},
                         {"subscriberId": subscriber_id}]
        last_resp = None
        for body in body_variants:
            resp = self._post(
                "/PSAPI/internal/v1/licy-base-private/subslcstates/history/search",
                params=params,
                json_body=body,
            )
            last_resp = resp
            if resp.status_code == 200:
                data = self._safe_json(resp)
                if isinstance(data, dict) and (data.get("items") or data.get("listInfo")):
                    return data
                if isinstance(data, dict):
                    return data
            # 4xx — пробуем следующий вариант
        if last_resp is not None and last_resp.status_code != 200:
            print(f"[WARN] lifecycle_history HTTP {last_resp.status_code}: "
                  f"{last_resp.text[:200] if last_resp.text else ''}")
        return self._safe_json(last_resp) if last_resp is not None else None

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

    @staticmethod
    def _parse_quota_xml(text):
        """Парсер ответа `UCELL_GET_SUBSCRIBER_ALL_QUOTA`.

        Реальная структура (см. дамп с прода):

            <SELFCARE>
              ...meta...
              <UCELL_GET_SUBSCRIBER_ALL_QUOTA>
                <RESULT>1</RESULT>
                <PCRF_SUBSCRIBER_GET>
                  <SUBSCRIBER_QUOTA>
                    <QTANAME>Q_Un_Soc_Msg</QTANAME>
                    <QTAVALUE>2147483648</QTAVALUE>
                    <QTACONSUMPTION>426805</QTACONSUMPTION>
                    <QTABALANCE>2147056843</QTABALANCE>
                    <QTASTARTDATETIME>2026-05-06T16:11:33</QTASTARTDATETIME>
                    <QTARSTDATETIME>-1--T::</QTARSTDATETIME>
                    <SRVNAME>S_Un_Soc_Msg</SRVNAME>
                  </SUBSCRIBER_QUOTA>
                  <SUBSCRIBER_QUOTA>...</SUBSCRIBER_QUOTA>
                </PCRF_SUBSCRIBER_GET>
              </UCELL_GET_SUBSCRIBER_ALL_QUOTA>
            </SELFCARE>

        Возвращает (items, meta) либо (None, meta_with_error).
        items — список словарей с raw-полями. meta — {result, count, block}.
        """
        if not text:
            return None, {"error": "empty"}
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            return None, {"error": f"xml parse: {str(e)[:120]}"}

        proc = root.find(".//UCELL_GET_SUBSCRIBER_ALL_QUOTA")
        if proc is None:
            # Иногда блок может быть на верхнем уровне, без SELFCARE-обёртки —
            # но фактически на проде есть всегда. Если нет — wrapper-only.
            return None, {"error": "no UCELL_GET_SUBSCRIBER_ALL_QUOTA block"}

        meta = {"block": "UCELL_GET_SUBSCRIBER_ALL_QUOTA"}
        result_el = proc.find("RESULT")
        if result_el is not None and result_el.text:
            meta["result"] = result_el.text.strip()

        items = []
        # SUBSCRIBER_QUOTA-элементы могут лежать под PCRF_SUBSCRIBER_GET либо
        # сразу под процедурой — ищем рекурсивно.
        for sq in proc.iter("SUBSCRIBER_QUOTA"):
            row = {}
            for child in sq:
                if list(child):
                    # вложенный объект — пропускаем (не ожидается в quota)
                    continue
                row[child.tag] = (child.text or "").strip() if child.text else None
            if row:
                items.append(row)
        meta["count"] = len(items)
        return items, meta

    def get_subscriber_all_quota(self, subscriber_id=None, msisdn=None, imsi=None):
        """Все квоты/остатки/бонусы абонента — `UCELL_GET_SUBSCRIBER_ALL_QUOTA`.

        Реальный путь, по которому SBMS UI зовёт эту процедуру:
          GET /PSIX/ucell/UCELL_GET_SUBSCRIBER_ALL_QUOTA
                ?IMSI=434054443280878
                &MSISDN=998500061400
                &SESSION_ID=<token>

        Параметры IMSI и MSISDN обязательны. IMSI берётся из
        `/OAPI/v1/subscribers/{sid}?fields=...mainSIMCard` (если не передан явно).

        Возвращает dict:
          { items: [...], count, http_status, path,
            imsi, msisdn, attempts: [{path,status,snippet?}] }
        Если процедура вернула HTTP-ошибку или wrapper-only — items=[],
        в `error` ляжет краткая причина, а в `attempts` — детали для UI.
        """
        out = {"items": [], "count": 0, "attempts": []}

        # Резолвим IMSI, если не передан
        if not imsi and subscriber_id:
            try:
                info = self.get_subscriber_identification(subscriber_id) or {}
                ts = (info.get("typeSpecificAttributes") or {})
                sim = (ts.get("mainSIMCard") or {})
                imsi = sim.get("imsi") or sim.get("IMSI")
                if not msisdn:
                    msisdn = info.get("identification") or msisdn
            except Exception as e:
                out["attempts"].append({
                    "path": f"/OAPI/v1/subscribers/{subscriber_id}",
                    "error": "identification fetch failed: " + str(e)[:120],
                })

        if not imsi or not msisdn:
            out["error"] = "Не удалось получить IMSI/MSISDN абонента (нужны для UCELL_GET_SUBSCRIBER_ALL_QUOTA)"
            return out

        path = "/PSIX/ucell/UCELL_GET_SUBSCRIBER_ALL_QUOTA"
        params = {
            "IMSI":       imsi,
            "MSISDN":     msisdn,
            "SESSION_ID": self.token,
        }
        # Через PSIX-helper: persistent Session + warm-up + retry на 401/403/404.
        # Это снимает основной источник «404 на корректном URL» —
        # отсутствие PHPSESSID-cookie и Referer-header.
        resp, call_attempts = self._psix_call(path, params=params)
        for a in call_attempts:
            out["attempts"].append({"path": path, **{k: v for k, v in a.items() if k != "url"}})

        if resp is None:
            out["http_status"] = None
            out["error"] = "Сетевая ошибка при обращении к PSIX"
            out["imsi"], out["msisdn"] = imsi, msisdn
            return out

        text = resp.text or ""

        if resp.status_code != 200:
            # Снапшот snippet'а (после redact) кладём в последнюю attempt
            if out["attempts"]:
                out["attempts"][-1]["snippet"] = _psix_safe_snippet(text, max_len=200)
            out["http_status"] = resp.status_code
            # Маркер для UI/server, чтобы можно было различать «404 = нужно
            # переавторизоваться» и «404 = действительно нет URL».
            if resp.status_code in (401, 403):
                out["error_kind"] = "auth_expired"
                out["error"] = f"Токен/сессия отклонены SBMS (HTTP {resp.status_code})"
            elif resp.status_code == 404:
                # 404 после двух попыток с warm-up = либо токен невалиден,
                # либо у учётки нет прав на эту процедуру. Чаще — первое.
                out["error_kind"] = "auth_or_permission"
                out["error"] = (
                    "PSIX вернул 404 даже после обновления сессии. "
                    "Вероятно, истёк authToken — переавторизуйтесь. "
                    "Если ошибка повторяется — у учётки нет прав на UCELL_GET_SUBSCRIBER_ALL_QUOTA."
                )
            else:
                out["error_kind"] = "upstream_error"
                out["error"] = f"HTTP {resp.status_code} @ {path}"
            out["imsi"], out["msisdn"] = imsi, msisdn
            return out

        out["http_status"] = 200
        out["path"] = path
        out["imsi"], out["msisdn"] = imsi, msisdn

        if not text.strip():
            if out["attempts"]:
                out["attempts"][-1]["empty"] = True
            out["error_kind"] = "empty_body"
            out["error"] = "HTTP 200 empty body"
            return out

        # Helper: метаданные парсинга кладём в последнюю записанную попытку
        # (helper _psix_call уже добавил attempts в out['attempts']).
        last_attempt = out["attempts"][-1] if out["attempts"] else None

        # JSON?
        try:
            j = resp.json()
            items = None
            if isinstance(j, dict):
                items = j.get("items") or j.get("rows") or j.get("data")
                # Иногда SBMS оборачивает в SELFCARE-подобный JSON-ключ
                if not items:
                    for k, v in j.items():
                        if isinstance(v, list) and v and isinstance(v[0], dict):
                            items = v
                            break
            elif isinstance(j, list):
                items = j
            if isinstance(items, list):
                if last_attempt is not None:
                    last_attempt["parsed"] = "json"
                    last_attempt["count"] = len(items)
                out["items"] = items
                out["count"] = len(items)
                return out
        except Exception:
            pass

        # XML SELFCARE — используем спец-парсер под структуру
        # UCELL_GET_SUBSCRIBER_ALL_QUOTA → PCRF_SUBSCRIBER_GET → SUBSCRIBER_QUOTA
        items, meta = self._parse_quota_xml(text)
        if items is not None:
            if last_attempt is not None:
                last_attempt["parsed"] = "xml"
                last_attempt["count"] = len(items)
                if meta.get("result"):
                    last_attempt["result"] = meta["result"]
                if meta.get("block"):
                    last_attempt["block"] = meta["block"]
            if meta.get("block"):
                out["block"] = meta["block"]
            if meta.get("result"):
                out["result"] = meta["result"]
            out["items"] = items
            out["count"] = len(items)
            # SBMS возвращает RESULT=1 при успехе; всё остальное (например, 0
            # или -1) — ошибка процедуры, но это не наша проблема: процедура
            # отработала, items пустые → UI покажет «нет квот».
            return out

        # _parse_quota_xml сообщил об ошибке — wrapper-only либо чужая структура
        if last_attempt is not None:
            last_attempt["parsed"] = "unknown"
            last_attempt["snippet"] = _psix_safe_snippet(text, max_len=300)
            if meta.get("error"):
                last_attempt["parse_error"] = meta["error"]
        out["error"] = "Не удалось распарсить ответ ({})".format(meta.get("error") or "wrapper-only")
        return out

    # ======================== PSIX CALLS (события абонента) ========================

    def get_calls(self, subscriber_id, customer_id=None, msisdn=None,
                  start_date=None, end_date=None, limit=500, offset=0,
                  debug=False):
        """События/вызовы (CDR) абонента — `POST /PSIX/grid/STRAFF_CALLS_R`.

        Полностью имитирует то, как HAS-SBMS UI шлёт запрос:
          1. `requests.Session()` — общие cookies между warm-up и основным
             запросом (PHP/Java backend держит состояние формы в session).
          2. `Authorization: Basic` — без него grid-процедура отдаёт
             899-байтовый SELFCARE-wrapper.
          3. Warm-up `GET /PSIX/grid/STRAFF_INIT_FORM_PARAMS_R` — регистрирует
             контекст формы; без него STRAFF_CALLS_R может отдавать
             валидный, но пустой dataset.
          4. `Origin` / `Referer` / `X-Requested-With` — некоторые сборки
             SBMS проверяют их на CSRF.
          5. Авто-расширение периода: если 60-дневный диапазон пуст —
             пробуем 180 и 540 дней.

        Запасной канал — `GET /PSIX/scli/UCL_OAPI_GET_CHARGE` по MSISDN.
        """
        from datetime import datetime, timedelta

        # Параметризованный период: основной + расширения для авто-ретрая.
        now_dt = datetime.now()
        user_end_dt = None
        user_start_dt = None
        try:
            if end_date:
                user_end_dt = datetime.strptime(end_date, "%d.%m.%Y %H:%M:%S")
            if start_date:
                user_start_dt = datetime.strptime(start_date, "%d.%m.%Y %H:%M:%S")
        except Exception:
            user_end_dt = None
            user_start_dt = None

        end_dt = user_end_dt or now_dt
        # Список периодов: исходный (или 60 дн), затем 180 и 540 дн назад.
        if user_start_dt:
            base_days = max(1, int((end_dt - user_start_dt).total_seconds() // 86400) or 60)
        else:
            base_days = 60
        period_days = sorted({base_days, 180, 540})

        def _fmt(d):
            return d.strftime("%d.%m.%Y %H:%M:%S")

        start_row = int(offset) + 1
        end_row = int(offset) + int(limit)

        # PL/SQL-таблица в STRAFF_CALLS_R держит максимум 500 строк за запрос —
        # на 501-й сервер отдаёт ORA-06513 (index for PL/SQL table out of range).
        # Если запрошено больше — пагинируем и склеиваем на нашей стороне.
        MAX_PAGE = 500

        attempts = []
        winner = None

        auth = None
        if self._login and self._password:
            auth = (self._login, self._password)

        # === Подготавливаем Session с persistent cookies + общими заголовками ===
        sess = requests.Session()
        sess.verify = False
        if auth:
            sess.auth = auth
        common_headers = {
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "*/*",
            "Accept-Language": "ru,en;q=0.9",
        }

        # === Warm-up: STRAFF_INIT_FORM_PARAMS_R (регистрирует контекст формы) ===
        try:
            warmup_url = f"{self.base_url}/PSIX/grid/STRAFF_INIT_FORM_PARAMS_R"
            wresp = sess.get(
                warmup_url,
                params={
                    "SESSION_ID": self.token,
                    "INTM": "BIS",
                    "REM_IOPER_ID": 1,
                },
                headers=common_headers,
                timeout=self.timeout,
            )
            attempts.append({
                "method": "GET", "path": "/PSIX/grid/STRAFF_INIT_FORM_PARAMS_R",
                "mode": "warmup+basic" if auth else "warmup",
                "status": wresp.status_code, "len": len(wresp.text or ""),
                "snippet": _psix_safe_snippet(wresp.text or "", max_len=200),
                "count": 0,
            })
        except Exception as e:
            attempts.append({
                "method": "GET", "path": "/PSIX/grid/STRAFF_INIT_FORM_PARAMS_R",
                "mode": "warmup", "error": str(e),
            })

        url = f"{self.base_url}/PSIX/grid/STRAFF_CALLS_R"
        post_headers = dict(common_headers)
        post_headers["Content-Type"] = "application/x-www-form-urlencoded"

        # === Основные попытки: с расширяющимся периодом ===
        for days in period_days:
            attempt_start = (end_dt - timedelta(days=days))
            sd = _fmt(attempt_start)
            ed = _fmt(end_dt)

            # Пагинируем: каждая страница максимум MAX_PAGE строк.
            page_items = []
            page_block = None
            page_count = 0
            page_failed = False
            page_start = start_row
            while page_start <= end_row:
                page_end = min(page_start + MAX_PAGE - 1, end_row)
                body = {
                    "I_BEGIN_DATE": sd,
                    "I_END_DATE":   ed,
                    "I_CLT_ID":     customer_id or "",
                    "I_SUBS_ID":    subscriber_id or "",
                    "I_STND_ID":    1,
                    "I_COST_PLUS":  0,
                    "I_CALL_OUT":   0,
                    "I_TIME_ZONE":  0,
                    "R_MSISDN":     0,
                    "P_CHARGE_MODE": 0,
                    "I_ORDER":      -5,
                    "I_START_ROW":  page_start,
                    "I_END_ROW":    page_end,
                    "SESSION_ID":   self.token,
                    "INTM":         "BIS",
                    "REM_IOPER_ID": 1,
                }
                try:
                    resp = sess.post(
                        url, data=body, headers=post_headers, timeout=self.timeout,
                    )
                    text = resp.text or ""
                    parsed = self._parse_calls_xml(text)
                    page_n_items = (parsed or {}).get("items") or []
                    page_n_count = (parsed or {}).get("count", 0)
                    entry = {
                        "method": "POST", "path": "/PSIX/grid/STRAFF_CALLS_R",
                        "mode": "form+basic" if auth else "form",
                        "period_days": days,
                        "begin": sd, "end": ed,
                        "page": [page_start, page_end],
                        "status": resp.status_code, "len": len(text),
                        "snippet": _psix_safe_snippet(text, max_len=400),
                        "count": page_n_count,
                    }
                    if parsed and parsed.get("block"):
                        entry["block"] = parsed["block"]
                        page_block = parsed["block"]
                    attempts.append(entry)
                    if page_n_count > 0 and page_n_items:
                        page_items.extend(page_n_items)
                        page_count += page_n_count
                        # Если страница недозаполнена — данных в этом периоде больше нет.
                        if len(page_n_items) < (page_end - page_start + 1):
                            break
                        page_start = page_end + 1
                    else:
                        # Пустая страница: либо нет данных в периоде, либо ошибка.
                        # В обоих случаях — прекращаем пагинировать этот период.
                        if page_count == 0:
                            page_failed = True
                        break
                except Exception as e:
                    attempts.append({
                        "method": "POST", "path": "/PSIX/grid/STRAFF_CALLS_R",
                        "mode": "form+basic" if auth else "form",
                        "period_days": days,
                        "page": [page_start, page_end],
                        "error": str(e),
                    })
                    page_failed = True
                    break

            if page_count > 0 and not page_failed:
                winner = {
                    "items": page_items,
                    "count": page_count,
                    "block": page_block,
                    "period_days": days,
                    "begin": sd,
                    "end": ed,
                }
                if not debug:
                    winner["attempts"] = attempts
                    return winner
            # Иначе пробуем следующий (расширенный) период.

        # === Fallback по MSISDN (если Basic-учётки не было в кеше) ===
        if msisdn and winner is None:
            try:
                fb_url = f"{self.base_url}/PSIX/scli/UCL_OAPI_GET_CHARGE"
                # Берём максимальный период для шанса найти данные
                max_days = period_days[-1]
                fb_start = _fmt(end_dt - timedelta(days=max_days))
                fb_end = _fmt(end_dt)
                resp = sess.get(fb_url, params={
                    "SESSION_ID": self.token,
                    "SUBSCRIBER_MSISDN": msisdn,
                    "P_DATE_FROM": fb_start,
                    "P_DATE_TILL": fb_end,
                    "P_SUBS_ID": subscriber_id or "",
                }, headers=common_headers, timeout=self.timeout)
                text = resp.text or ""
                parsed = self._parse_calls_xml(text)
                entry = {
                    "method": "GET", "path": "/PSIX/scli/UCL_OAPI_GET_CHARGE",
                    "mode": "fallback",
                    "status": resp.status_code, "len": len(text),
                    "snippet": _psix_safe_snippet(text, max_len=400),
                    "count": (parsed or {}).get("count", 0),
                }
                if parsed and parsed.get("block"):
                    entry["block"] = parsed["block"]
                attempts.append(entry)
                if parsed and parsed.get("count", 0) > 0:
                    winner = parsed
            except Exception as e:
                attempts.append({
                    "method": "GET", "path": "/PSIX/scli/UCL_OAPI_GET_CHARGE",
                    "mode": "fallback", "error": str(e),
                })

        out = winner or {"items": [], "count": 0}
        # Если был хоть один STRAFF_CALLS-ответ с распознанным блоком, но 0 строк —
        # это «процедура отработала, событий нет», а НЕ ошибка вызова.
        out["procedure_ok"] = any(
            a.get("block") for a in attempts
            if a.get("path", "").endswith("STRAFF_CALLS_R")
        )
        out["attempts"] = attempts
        return out

    # Системные теги SELFCARE-обёртки, которые НЕ являются датасетом
    _SELFCARE_META_TAGS = frozenset({
        "HOSTNAME", "CHANNEL", "SESSION_ID", "LOGIN", "ACCESS_LEVEL_ID",
        "LANG_ID", "HAS_GET_USER_ATTRIBUTES", "STATUS", "ERR_CODE", "ERR_TEXT",
        "MESSAGE", "SERVER_TIME", "USER_INFO",
    })

    def _parse_calls_xml(self, text):
        """Распарсить XML scli-ответа → {items, count, block} либо None.

        Логика:
          1. Берём первый не-мета дочерний элемент `<SELFCARE>` с `<ROW>` —
             это датасет процедуры. Возвращаем его строки.
          2. Если такого нет — ищем не-мета блок (`STRAFF_CALLS`, `COUNT`-
             элемент и т. п.). Это означает «процедура отработала, но
             ничего не вернула». Тогда отдаём block без items, чтобы
             вызывающий код понял разницу с «полная ерунда / wrapper-only».
        """
        if not text:
            return None
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return None

        ds_block = None
        empty_block = None
        for child in root:
            if child.tag in self._SELFCARE_META_TAGS:
                continue
            # ROW может лежать глубже: <DATASET><ROWSET><ROW>… либо
            # <DATASET><DATA><ROW>… и т. п. Ищем рекурсивно.
            if next(child.iter("ROW"), None) is not None and ds_block is None:
                ds_block = child
                break
            # Запоминаем первый «не-мета» блок как индикатор того,
            # что процедура отработала, даже если строк нет.
            if empty_block is None:
                empty_block = child

        if ds_block is not None:
            rows = []
            for row in ds_block.iter("ROW"):
                item = {}
                # 1) Атрибуты <ROW name="value" .../> — некоторые PSIX-grids
                # отдают именно так.
                for k, v in row.attrib.items():
                    item[k] = v
                # 2) Дочерние теги <ROW><FIELD>val</FIELD>...</ROW>.
                for f in row:
                    if list(f):
                        # Вложенный объект — собираем простой текст всех листьев.
                        leaf = " ".join(
                            (c.text or "").strip()
                            for c in f.iter()
                            if c is not f and c.text and c.text.strip()
                        )
                        item[f.tag] = leaf or None
                    else:
                        item[f.tag] = (f.text or "").strip() if f.text else None
                rows.append(item)
            return {"items": rows, "count": len(rows), "block": ds_block.tag}

        if empty_block is not None:
            return {"items": [], "count": 0, "block": empty_block.tag}

        return {"items": [], "count": 0}
