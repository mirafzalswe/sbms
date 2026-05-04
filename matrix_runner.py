#!/usr/bin/env python3
"""Раннер прогона «Матрицы переходов».

Алгоритм для каждой строки матрицы (исходный тариф):
1. admin_client.change_rateplan(subscriber_id, from_id)
2. ждать /orders → COMPLETED (с таймаутом)
3. viewer_client.get_available_rateplans(subscriber_id)
4. сравнить полученный список с expected{to_id: '+'|'-'} из строки матрицы
5. записать результаты ячеек

Результат — структура для отчёта + потоковые события для UI (через callback).
"""
from __future__ import annotations

import time
import json
import os
import uuid
from datetime import datetime
from typing import Callable

# Папка для отчётов матрицы
HISTORY_DIR = os.path.join(os.path.dirname(__file__), "test_history")
os.makedirs(HISTORY_DIR, exist_ok=True)


def _extract_available_ids(api_resp) -> dict[int, dict]:
    """Из ответа availableForChange/search вытащить {ratePlanId: {name, archived,
    activation_cost, fee_amount, fee_period}}.

    Архивные тарифы (isArchived=true) ПРОПУСКАЮТСЯ — они не доступны для перехода,
    хоть API иногда и возвращает их в общем списке. Иначе матрица переходов
    помечала их как «+» («доступно»), хотя по бизнес-логике должно быть «-».

    activation_cost — разовое начисление за переход (0, если бесплатно).
    fee_amount      — абонентская плата нового тарифа (subscriptionFeeDetail.amount).
    fee_period      — единица периода АП (например MONTH).
    """
    if not isinstance(api_resp, dict):
        return {}
    items = api_resp.get("items") or api_resp.get("data") or []
    out: dict[int, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        rp = item.get("ratePlan")
        if not rp:
            rp_id = item.get("ratePlanId")
            name = item.get("name", "")
            archived = item.get("isArchived", False)
        else:
            rp_id = rp.get("ratePlanId")
            name = rp.get("name", "")
            archived = item.get("isArchived", False) or rp.get("isArchived", False)
        if rp_id is None:
            continue

        # Пропускаем архивные — для матрицы переходов они «недоступны» (-)
        if bool(archived):
            continue

        # ratePlanCost: { activationCost, subscriptionFeeDetail: { amount, periodMeasureUnit, ... } }
        cost = item.get("ratePlanCost") or {}
        activation_cost = cost.get("activationCost")
        fee_detail = cost.get("subscriptionFeeDetail") or {}
        fee_amount = fee_detail.get("amount")
        fee_period = fee_detail.get("periodMeasureUnit")

        try:
            out[int(rp_id)] = {
                "name": name or "",
                "archived": False,
                "activation_cost": activation_cost,
                "fee_amount": fee_amount,
                "fee_period": fee_period,
            }
        except (TypeError, ValueError):
            continue
    return out


def _current_rateplan_id(admin_client, subscriber_id, msisdn=None):
    """Вернуть текущий ratePlanId абонента через searchBase.
    Это самый надёжный способ убедиться что тариф реально сменился.
    """
    try:
        # Сперва пробуем через subscriberId напрямую (если поддерживается)
        info = admin_client.get_subscriber_info(subscriber_id)
        if isinstance(info, dict):
            rp = (info.get("ratePlan") or {}).get("ratePlanId")
            if rp:
                return int(rp)
            rp = (info.get("firstSubscriber") or {}).get("ratePlan", {}).get("ratePlanId")
            if rp:
                return int(rp)
    except Exception:
        pass
    if not msisdn:
        return None
    try:
        s = admin_client.search_customer(msisdn) or {}
        sr = (s.get("searchResults") or [None])[0] or {}
        rp = ((sr.get("firstSubscriber") or {}).get("ratePlan") or {}).get("ratePlanId")
        return int(rp) if rp else None
    except Exception:
        return None


def _wait_order_complete(admin_client, subscriber_id, order_id, target_rp_id,
                         msisdn=None, timeout_sec=60, poll_sec=2):
    """Ожидаем смену тарифа двумя способами одновременно:
       1) /orders[order_id].status — статус заказа
       2) searchBase / subscriber → текущий ratePlanId == target_rp_id (главный критерий)

    Достаточно сработать одному из них. Возвращает (ok, status, elapsed, debug).
    """
    deadline = time.time() + timeout_sec
    last_status = None
    started = time.time()
    first_resp = None
    last_error = None
    poll_count = 0
    seen_statuses: list[str] = []  # все промежуточные статусы — для диагностики

    while time.time() < deadline:
        # === КРИТЕРИЙ 1: проверка фактического тарифа абонента ===
        current_rp = _current_rateplan_id(admin_client, subscriber_id, msisdn)
        if current_rp is not None and target_rp_id is not None and int(current_rp) == int(target_rp_id):
            debug = {
                "polls": poll_count, "first_resp": first_resp,
                "status_name": last_status or "RP_MATCHED",
                "status_id": None,
                "matched_via": "searchBase.ratePlanId",
                "current_rp": current_rp, "target_rp": target_rp_id,
                "seen_statuses": seen_statuses,
            }
            return True, last_status or "RP_MATCHED", time.time() - started, debug

        # === КРИТЕРИЙ 2: статус заказа ===
        try:
            raw = admin_client.get_rateplan_orders(subscriber_id)
            resp = raw or {}
            poll_count += 1
            if first_resp is None:
                first_resp = {
                    "raw": str(raw)[:800] if raw is not None else None,
                    "items_count": len(resp.get("items") or resp.get("data") or []),
                    "keys": list(resp.keys()) if isinstance(resp, dict) else None,
                }
            items = resp.get("items") or resp.get("data") or []
            for it in items:
                oid = it.get("ratePlanOrderId") or it.get("orderId") or it.get("id")
                if str(oid) == str(order_id):
                    raw_status = it.get("status") or it.get("orderStatus")
                    if isinstance(raw_status, dict):
                        status_name = str(raw_status.get("name") or "").strip().upper()
                        status_id   = raw_status.get("ratePlanOrderStatusId")
                    else:
                        status_name = str(raw_status or "").strip().upper()
                        status_id   = None
                    last_status = status_name or (str(status_id) if status_id else "UNKNOWN")
                    if last_status not in seen_statuses:
                        seen_statuses.append(last_status)

                    # ID-маппинг + явные имена
                    _DONE_IDS = {2, 4}                       # активен / выполнен
                    _FAIL_IDS = {3, 6, 7, 9}                 # ошибка / отменён
                    _DONE_HINTS = ("АКТИВ", "ВЫПОЛН", "ПОДКЛЮЧ", "ПРИМЕН",
                                   "COMPLET", "DONE", "SUCCESS", "FINISH")
                    _FAIL_HINTS = ("ОШИБК", "ОТМЕН", "ОТКЛОН", "FAIL", "REJECT",
                                   "ERROR", "CANCEL")

                    is_done = status_id in _DONE_IDS or any(h in status_name for h in _DONE_HINTS)
                    is_fail = status_id in _FAIL_IDS or any(h in status_name for h in _FAIL_HINTS)
                    if is_done:
                        debug = {"polls": poll_count, "first_resp": first_resp,
                                 "status_id": status_id, "status_name": status_name,
                                 "matched_via": "order.status",
                                 "seen_statuses": seen_statuses}
                        return True, last_status, time.time() - started, debug
                    if is_fail:
                        debug = {"polls": poll_count, "first_resp": first_resp,
                                 "status_id": status_id, "status_name": status_name,
                                 "matched_via": "order.status",
                                 "seen_statuses": seen_statuses}
                        return False, last_status, time.time() - started, debug
                    break
        except Exception as exc:
            last_error = str(exc)
        time.sleep(poll_sec)

    # Тайм-аут — но в самом конце ещё раз проверяем фактический тариф
    current_rp = _current_rateplan_id(admin_client, subscriber_id, msisdn)
    if current_rp is not None and target_rp_id is not None and int(current_rp) == int(target_rp_id):
        debug = {
            "polls": poll_count, "first_resp": first_resp,
            "status_name": last_status or "RP_MATCHED_LATE",
            "matched_via": "searchBase.ratePlanId (after timeout)",
            "current_rp": current_rp, "target_rp": target_rp_id,
            "seen_statuses": seen_statuses,
        }
        return True, last_status or "RP_MATCHED_LATE", time.time() - started, debug

    debug = {
        "polls": poll_count,
        "first_resp": first_resp,
        "last_error": last_error,
        "order_id_searched": str(order_id),
        "target_rp": target_rp_id,
        "current_rp": current_rp,
        "seen_statuses": seen_statuses,
    }
    return False, last_status or "TIMEOUT", time.time() - started, debug


def _emit(cb: Callable | None, **payload):
    if cb:
        try:
            cb(payload)
        except Exception:
            pass


def _report_path(report_id: str) -> str:
    return os.path.join(HISTORY_DIR, f"matrix_{report_id}.json")


def _save_report_atomic(report: dict) -> str | None:
    """Атомарная запись отчёта (через временный файл + rename), чтобы не остаться
    с обрезанным JSON, если процесс убьют ровно во время записи."""
    rid = report.get("id")
    if not rid:
        return None
    path = _report_path(rid)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return path
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return None


class AbortRun(Exception):
    """Сигнал «корректно завершить прогон сейчас»; вызывает прерывание ДО следующей строки.
    Уже отработанные строки сохранены и доступны для resume."""


def run_matrix(
    *,
    msisdn: str,
    spec: dict,
    admin_client,
    viewer_client,
    only_rows: list | None = None,
    wait_after_change: float = 5.0,
    order_timeout: int = 60,
    progress_cb: Callable | None = None,
    resume_from: str | None = None,
    abort_event=None,
) -> dict:
    """Полный прогон. Возвращает report dict.

    Параметры:
    - msisdn — номер абонента, на котором гоняем (один!).
    - spec — каноничный JSON матрицы (см. matrix_parser).
    - admin_client / viewer_client — два авторизованных SBMSClient.
    - only_rows — список row.id, которые надо прогнать (None = все).
    - wait_after_change — пауза после COMPLETED перед запросом availableForChange.
    - progress_cb — callback({event, row, ...}) для UI-стрима.
    - resume_from — id отчёта для продолжения. Тогда: переиспользуем тот же отчёт,
      пропускаем уже сделанные строки (по row_id), приклеиваем новые результаты к
      старым. Спецификация (msisdn, columns) должна совпадать — иначе RuntimeError.
    - abort_event — threading.Event(); если set() — прогон корректно
      завершится между строками, частичный отчёт уже сохранён на диск.
    """
    # ---- Resume vs new report ---------------------------------------------
    prior_results: list[dict] = []
    prior_counters = {"pass": 0, "fail": 0, "skip": 0, "blocked": 0}
    done_row_ids: set[int] = set()

    if resume_from:
        prior = get_matrix_report(resume_from)
        if not prior:
            raise RuntimeError(f"Отчёт {resume_from} не найден — невозможно продолжить")
        if (prior.get("msisdn") or "") != msisdn:
            raise RuntimeError(
                f"MSISDN отчёта ({prior.get('msisdn')}) не совпадает с текущим ({msisdn}) — "
                "продолжение возможно только на том же номере"
            )
        report_id = prior["id"]
        started_at = prior.get("started_at") or (datetime.utcnow().isoformat() + "Z")
        prior_results = list(prior.get("results") or [])
        # Учитываем только успешно завершённые (включая blocked — admin не смог,
        # повторять не имеет смысла, пользователь может отдельно перезапустить).
        for r in prior_results:
            try:
                done_row_ids.add(int(r.get("row_id")))
            except (TypeError, ValueError):
                pass
        prior_counters = dict(prior.get("counters") or prior_counters)
        for k in ("pass", "fail", "skip", "blocked"):
            prior_counters.setdefault(k, 0)
    else:
        report_id = uuid.uuid4().hex[:12]
        started_at = datetime.utcnow().isoformat() + "Z"

    # Резолв абонента
    search = admin_client.search_customer(msisdn) or {}
    sr = (search.get("searchResults") or [None])[0]
    if not sr:
        raise RuntimeError(f"Абонент с MSISDN {msisdn} не найден (admin)")
    subscriber_id = sr.get("subscriberId")
    customer_id = sr.get("customerId")
    initial_rateplan = ((sr.get("firstSubscriber") or {}).get("ratePlan") or {}).get("ratePlanId")
    customer_name = sr.get("name") or sr.get("customerName") or ""

    if not subscriber_id:
        raise RuntimeError("Не удалось определить subscriberId абонента")

    rows = spec.get("rows") or []
    cols = spec.get("columns") or []
    if only_rows:
        only_set = {int(x) for x in only_rows}
        rows = [r for r in rows if int(r.get("id")) in only_set]

    # При resume пропускаем уже сделанные строки
    skipped_done = 0
    if done_row_ids:
        before = len(rows)
        rows = [r for r in rows if int(r.get("id")) not in done_row_ids]
        skipped_done = before - len(rows)

    total_cells = len(rows) * len(cols)
    _emit(progress_cb, event="start",
          total_rows=len(rows), total_cells=total_cells,
          subscriber_id=subscriber_id, customer_name=customer_name,
          initial_rateplan=initial_rateplan,
          report_id=report_id, resumed=bool(resume_from), skipped_done=skipped_done,
          done_row_ids=sorted(done_row_ids))

    results: list[dict] = list(prior_results)
    counters = dict(prior_counters)
    t0 = time.time()

    def _is_aborted() -> bool:
        try:
            return bool(abort_event is not None and abort_event.is_set())
        except Exception:
            return False

    def _build_partial_report(*, finished: bool) -> dict:
        return {
            "id": report_id,
            "type": "matrix",
            "msisdn": msisdn,
            "subscriber_id": subscriber_id,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "initial_rateplan": initial_rateplan,
            "started_at": started_at,
            "finished_at": (datetime.utcnow().isoformat() + "Z") if finished else None,
            "duration": round(time.time() - t0, 2),
            "spec": {"columns": cols, "rows_total": len(rows) + len(prior_results)},
            "results": results,
            "counters": counters,
            "status": "completed" if finished else "in_progress",
            "resumable": not finished,
        }

    for row_idx, row in enumerate(rows):
        # Проверка abort ПЕРЕД началом строки. Никаких прерываний посреди
        # уже стартовавшей строки — иначе тариф может остаться полу-переключённым.
        if _is_aborted():
            partial = _build_partial_report(finished=False)
            saved = _save_report_atomic(partial)
            if saved:
                partial["_saved_path"] = saved
            _emit(progress_cb, event="aborted", report=partial,
                  reason="user_stop", processed=len(results) - len(prior_results),
                  remaining=len(rows) - row_idx)
            return partial

        from_id = int(row["id"])
        from_name = row.get("name") or str(from_id)
        row_started = time.time()
        _emit(progress_cb, event="row_start", row_index=row_idx, row_id=from_id, row_name=from_name)

        # 1) Сменить тариф под админом
        change_resp = admin_client.change_rateplan(subscriber_id, from_id)
        order_id = None
        if isinstance(change_resp, dict):
            order_id = (
                change_resp.get("ratePlanOrderId")
                or change_resp.get("orderId")
                or change_resp.get("id")
            )

        change_ok = bool(order_id) and "error" not in (change_resp or {})
        order_status = None
        wait_elapsed = 0.0
        wait_debug = {}
        if change_ok:
            ok, order_status, wait_elapsed, wait_debug = _wait_order_complete(
                admin_client, subscriber_id, order_id,
                target_rp_id=from_id, msisdn=msisdn,
                timeout_sec=order_timeout, poll_sec=2,
            )
            change_ok = ok
        else:
            # order_id не получен — сразу blocked
            wait_debug = {"order_id_from_resp": str(order_id), "change_resp_keys": list((change_resp or {}).keys())}

        if not change_ok:
            # вся строка blocked
            row_result = {
                "row_id": from_id, "row_name": from_name,
                "blocked": True,
                "block_reason": f"change failed (status={order_status or 'no_order_id'})",
                "change_response": change_resp,
                "wait_debug": wait_debug,
                "available_ids": [],
                "cells": [
                    {"to_id": c["id"], "to_name": c["name"],
                     "expected": (row.get("expected") or {}).get(str(c["id"]), ""),
                     "actual": "", "status": "blocked"}
                    for c in cols
                ],
                "duration": round(time.time() - row_started, 2),
            }
            results.append(row_result)
            counters["blocked"] += len(cols)
            # Инкрементальный save: даже blocked-строка фиксируется на диске,
            # чтобы при resume её не повторять зря (admin всё равно не смог).
            _save_report_atomic(_build_partial_report(finished=False))
            _emit(progress_cb, event="row_done", row_index=row_idx, row=row_result,
                  counters=counters, elapsed=round(time.time()-t0, 1))
            continue

        # 2) Дождаться (мягко) применения изменений
        if wait_after_change > 0:
            time.sleep(wait_after_change)

        # 3) Запросить доступные тарифы под viewer'ом.
        # GET /availableForChange — отдаёт activationCost + subscriptionFeeDetail (fees).
        # POST /availableForChange/search — fees НЕ возвращает, поэтому используем GET.
        viewer_resp = None
        viewer_err = None
        try:
            viewer_resp = viewer_client.get_available_rateplans_with_fees(subscriber_id)
        except Exception as e:
            viewer_err = f"with_fees: {e}"
        # Если GET-вариант не сработал — fallback на старый POST/search (без fees)
        if not viewer_resp:
            try:
                viewer_resp = viewer_client.get_available_rateplans(subscriber_id)
            except Exception as e:
                viewer_err = (viewer_err or "") + f" | search: {e}"
        available = _extract_available_ids(viewer_resp or {})

        # 4) Сравнить с ожиданием матрицы
        cells = []
        for col in cols:
            to_id = int(col["id"])
            to_name = col.get("name") or str(to_id)
            expected = (row.get("expected") or {}).get(str(to_id), "")
            in_list = to_id in available
            is_self = (to_id == from_id)  # диагональ: целевой == текущий

            # Статус ячейки:
            # - диагональ (целевой = текущий тариф) → info, API не отдаёт сам себя
            # - +/+ → pass; -/- → pass; -/+ → fail (видим лишнее); +/- → fail (нет ожидаемого)
            # - пустое expected → info (ничего не ожидали — просто фиксируем факт)
            if is_self:
                status = "info"
            elif not expected:
                status = "info"
            elif expected == "+" and in_list:
                status = "pass"
            elif expected == "-" and not in_list:
                status = "pass"
            else:
                status = "fail"

            avail_info = available.get(to_id, {}) or {}
            cell = {
                "to_id": to_id, "to_name": to_name,
                "expected": expected,
                # На диагонали показываем «=» (текущий тариф) — иначе обычный +/-.
                "actual": "=" if is_self else ("+" if in_list else "-"),
                "status": status,
                "is_self": is_self,
                "archived": bool(avail_info.get("archived")),
                # Стоимость перехода — есть только если тариф реально доступен
                "activation_cost": avail_info.get("activation_cost") if in_list else None,
                "fee_amount":      avail_info.get("fee_amount")      if in_list else None,
                "fee_period":      avail_info.get("fee_period")      if in_list else None,
            }
            if status == "pass":
                counters["pass"] += 1
            elif status == "fail":
                counters["fail"] += 1
            else:
                counters["skip"] += 1
            cells.append(cell)

        # Диагностика: сколько ячеек реально получили fee/cost от SBMS
        cells_with_fees = sum(1 for c in cells if c.get("activation_cost") is not None or c.get("fee_amount") is not None)
        row_result = {
            "row_id": from_id, "row_name": from_name,
            "blocked": False,
            "order_id": order_id,
            "order_status": order_status,
            "wait_order_sec": round(wait_elapsed, 2),
            "available_ids": sorted(available.keys()),
            "available_count": len(available),
            "cells_with_fees": cells_with_fees,
            "viewer_error": viewer_err,
            "cells": cells,
            "duration": round(time.time() - row_started, 2),
        }
        results.append(row_result)
        # Инкрементальный save после каждой строки — отчёт всегда на диске.
        _save_report_atomic(_build_partial_report(finished=False))
        _emit(progress_cb, event="row_done", row_index=row_idx, row=row_result,
              counters=counters, elapsed=round(time.time()-t0, 1))

    # Финальный отчёт: status=completed, resumable=false
    report = _build_partial_report(finished=True)
    saved = _save_report_atomic(report)
    if saved:
        report["_saved_path"] = saved
    else:
        report["_save_error"] = "atomic write failed"

    _emit(progress_cb, event="done", report=report)
    return report


def list_matrix_reports() -> list[dict]:
    """Список последних JSON-отчётов матрицы."""
    out = []
    if not os.path.isdir(HISTORY_DIR):
        return out
    for f in sorted(os.listdir(HISTORY_DIR), reverse=True):
        if not (f.startswith("matrix_") and f.endswith(".json")):
            continue
        p = os.path.join(HISTORY_DIR, f)
        try:
            with open(p, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            out.append({
                "id": d.get("id"),
                "msisdn": d.get("msisdn"),
                "started_at": d.get("started_at"),
                "finished_at": d.get("finished_at"),
                "duration": d.get("duration"),
                "counters": d.get("counters"),
                "rows": d.get("spec", {}).get("rows_total"),
                "status": d.get("status") or ("completed" if d.get("finished_at") else "in_progress"),
                "resumable": bool(d.get("resumable")),
            })
        except Exception:
            continue
    return out


def get_matrix_report(report_id: str) -> dict | None:
    p = os.path.join(HISTORY_DIR, f"matrix_{report_id}.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None
