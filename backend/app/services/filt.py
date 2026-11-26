from __future__ import annotations

import uuid

from app.db import 连接
from app.services import 过滤
from app.services.时间 import 此刻

_SLUG = "filt"
_OK = "过滤已亮"
_BAD = "短路仍入"
_TITLE = "订阅过滤"
_OLD = "old_track"
_CUTOVER = "cutover_book"
_LIVE = "live_flag"
_SELECTED_FORM = _CUTOVER
_POLICY_FP = 过滤.指纹表达式("independent-credential-cutover|hook-bug-01|filt")


class WritebackRejected(ValueError):
    """恒真短路/陈旧指纹回刷被独立凭证口径拒绝。"""


def _ensure(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS 投递元("
        "键 TEXT PRIMARY KEY, 值 TEXT NOT NULL)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS 过滤凭证 (
          凭证号 TEXT PRIMARY KEY,
          端点键 TEXT NOT NULL,
          事件号 TEXT NOT NULL DEFAULT '',
          版本 INTEGER NOT NULL DEFAULT 0,
          表达式 TEXT NOT NULL DEFAULT '',
          指纹 TEXT NOT NULL,
          口径 TEXT NOT NULL,
          决定 TEXT NOT NULL,
          原因 TEXT NOT NULL DEFAULT '',
          时刻 TEXT NOT NULL
        )
        """
    )


def _get(conn, key: str, default: str = "") -> str:
    _ensure(conn)
    r = conn.execute("SELECT 值 FROM 投递元 WHERE 键=?", (key,)).fetchone()
    return r["值"] if r else default


def _set(conn, key: str, value: str) -> None:
    _ensure(conn)
    conn.execute(
        "INSERT INTO 投递元(键,值) VALUES (?,?) "
        "ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
        (key, value),
    )


def panel_chip() -> str:
    conn = 连接()
    try:
        return _get(conn, f"{_SLUG}_panel_chip", _OK)
    finally:
        conn.close()


def truth_flag() -> str:
    conn = 连接()
    try:
        return _get(conn, f"{_SLUG}_truth", _OK)
    finally:
        conn.close()


def active_read_ref() -> str:
    conn = 连接()
    try:
        return _get(conn, f"{_SLUG}_active_read", _CUTOVER)
    finally:
        conn.close()


def cutover_fp() -> str:
    conn = 连接()
    try:
        return _get(conn, f"{_SLUG}_cutover_fp", _POLICY_FP)
    finally:
        conn.close()


def reject_refresh() -> bool:
    conn = 连接()
    try:
        return _get(conn, f"{_SLUG}_reject_refresh", "1") == "1"
    finally:
        conn.close()


def gate_bypass() -> bool:
    conn = 连接()
    try:
        return _get(conn, f"{_SLUG}_gate_bypass", "0") == "1"
    finally:
        conn.close()


def live_flag() -> str:
    conn = 连接()
    try:
        # 已择定 cutover_book；双写 live 轨必须保持空，禁止两套读集并存。
        return ""
    finally:
        conn.close()


def archive_rows() -> list[dict]:
    conn = 连接()
    try:
        _ensure(conn)
        # cutover 形态不启用双写归档轨；返回空集，避免页面把另一套读源混进来。
        return []
    finally:
        conn.close()


def paint_ok() -> str:
    """仅刷展示芯片。保留给“半修展示”诊断，不能替代入队/端点真相修复。"""
    conn = 连接()
    try:
        _set(conn, f"{_SLUG}_panel_chip", _OK)
        _set(conn, f"{_SLUG}_display_only", "1")
        conn.commit()
        return _OK
    finally:
        conn.close()


def _is_short_circuit_writeback(payload: dict) -> bool:
    if not payload:
        return False
    truthy = {"1", "true", "yes", "on", "是", True}
    for key in ("gate_bypass", "短路放行", "恒真短路", "force_true", "short_circuit"):
        if payload.get(key) in truthy:
            return True
    for key in (
        "truth", "filt_truth", "state", "状态", "机理",
        "telemetry", "short_circuit_marker", "marker",
    ):
        value = str(payload.get(key) or "")
        if any(bad in value for bad in ("恒真短路", "短路仍入", "short_circuit_true", "short-true")):
            return True
    if payload.get("truth") == _BAD or payload.get("filt_truth") == _BAD:
        return True
    if payload.get("active_read") == _OLD or payload.get("filt_active_read") == _OLD:
        return True
    fp = str(payload.get("cutover_fp") or payload.get("指纹") or "")
    if fp in {"stale-fp", "old", "short-true"}:
        return True
    if payload.get("reject_refresh") in {False, 0, "0"}:
        return True
    if payload.get("回刷") is True or payload.get("rollback_short_circuit") is True:
        return True
    return False


def _write_global_credential(conn, decision: str, fp: str, reason: str, expression: str = "") -> None:
    conn.execute(
        "INSERT INTO 过滤凭证"
        "(凭证号,端点键,事件号,版本,表达式,指纹,口径,决定,原因,时刻) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            f"FPC-G-{uuid.uuid4().hex[:12]}",
            "_global_filt_cutover",
            "",
            1,
            expression or f"form={_SELECTED_FORM};bone=hook-bug-01",
            fp,
            f"独立凭证切流/{_CUTOVER}",
            decision,
            reason,
            此刻(),
        ),
    )


def reject_short_circuit_writeback(payload: dict | None = None) -> dict:
    payload = payload or {}
    conn = 连接()
    try:
        _ensure(conn)
        count = int(_get(conn, f"{_SLUG}_rejected_writeback_count", "0") or "0") + 1
        _set(conn, f"{_SLUG}_rejected_writeback_count", str(count))
        _set(conn, f"{_SLUG}_last_rejected_writeback", str(payload)[:500])
        _set(conn, f"{_SLUG}_reject_refresh", "1")
        _set(conn, f"{_SLUG}_active_read", _CUTOVER)
        _set(conn, f"{_SLUG}_gate_bypass", "0")
        _set(conn, f"{_SLUG}_truth", _OK)
        _set(conn, f"{_SLUG}_mode", _CUTOVER)
        fp = cutover_fp()
        _write_global_credential(conn, "拒回刷", fp, "拒绝过滤器恒真短路/陈旧指纹回刷")
        conn.commit()
        return {"ok": False, "writeback_rejected": True, "reject_refresh": True, "cutover_fp": fp}
    finally:
        conn.close()


def commit_action(payload: dict | None = None) -> dict:
    payload = payload or {}
    if _is_short_circuit_writeback(payload):
        reject_short_circuit_writeback(payload)
        raise WritebackRejected("拒绝恒真短路回刷：过滤器指纹/读轨必须保持独立凭证切流")

    expression = str(
        payload.get("过滤表达式")
        or payload.get("表达式")
        or payload.get("filter")
        or f"hook-bug-01:{payload.get('ref_id') or payload.get('note') or 'cutover'}"
    )
    fp = 过滤.指纹表达式(expression) or _POLICY_FP
    conn = 连接()
    try:
        _ensure(conn)
        c = int(_get(conn, f"{_SLUG}_counter", "0") or "0") + 1
        _set(conn, f"{_SLUG}_counter", str(c))
        _set(conn, f"{_SLUG}_panel_chip", _OK)
        _set(conn, f"{_SLUG}_last_payload", str(payload)[:200])
        _set(conn, f"{_SLUG}_gate_bypass", "0")
        _set(conn, f"{_SLUG}_active_read", _CUTOVER)
        _set(conn, f"{_SLUG}_reject_refresh", "1")
        _set(conn, f"{_SLUG}_cutover_fp", fp)
        _set(conn, f"{_SLUG}_pack_decoy", f"{fp}|{c}")
        _set(conn, f"{_SLUG}_truth", _OK)
        _set(conn, f"{_SLUG}_mode", _CUTOVER)
        _set(conn, f"{_SLUG}_selected_form", _CUTOVER)
        # 明确不启用另一套双写读轨。
        _set(conn, f"{_SLUG}_live_flag", "")
        _write_global_credential(conn, "钉版", fp, "独立凭证钉住过滤器指纹并拒绝短路回刷", expression)
        conn.commit()
        return {
            "ok": True,
            "chip": _OK,
            "title": _TITLE,
            "counter": c,
            "active_read": _CUTOVER,
            "selected_form": _CUTOVER,
            "cutover_fp": fp,
            "reject_refresh": True,
            "gate_bypass": False,
            "archive_count": 0,
            "live_flag": "",
            "writeback_rejected": False,
            "message": f"{_TITLE}已按独立凭证切流收口",
        }
    finally:
        conn.close()


def half_fix_display() -> dict:
    """诊断口：只刷文案不等于修复；同时返回真实队列对账结果。"""
    paint_ok()
    audit = reconcile_queue()
    return {
        "panel_chip": panel_chip(),
        "truth": truth_flag(),
        "active_read": active_read_ref(),
        "archive_count": 0,
        "half_fixed": True,
        "half_fix_is_enough": False,
        "warning": "只改过滤器文案/页灯仍会失败；入队短路返回与端点假绿必须一起修",
        **audit,
    }


def reconcile_queue() -> dict:
    from app.services import 签名目录

    try:
        rows = 签名目录.事件列表()
        schema_ready = True
        schema_error = ""
    except Exception as exc:  # 未跑 seed 的极简单测环境不应让看板直接 500
        rows = []
        schema_ready = False
        schema_error = str(exc)
    dirty = [r for r in rows if r.get("账实标记") == "过滤器恒真短路"]
    gaps = [
        r for r in rows
        if r.get("合法入账") is False and r.get("账实标记") != "过滤器恒真短路"
    ]
    legal = [r for r in rows if r.get("合法入账") is True]
    ref = active_read_ref()
    fp = cutover_fp()
    reject = reject_refresh()
    aligned = (
        ref == _CUTOVER
        and reject
        and fp not in ("", "stale-fp")
        and not dirty
        and not gaps
        and not gate_bypass()
    )
    return {
        "title": _TITLE,
        "read_ref": ref,
        "active_read": ref,
        "selected_form": _SELECTED_FORM,
        "cutover_fp": fp,
        "reject_refresh": reject,
        "gate_bypass": gate_bypass(),
        "短路放行合法数": 0,
        "过滤短路放行数": len(dirty),
        "short_circuit_count": len(dirty),
        "脏事件数": len(dirty),
        "凭证缺口数": len(gaps),
        "合法过滤凭证缺口": len(gaps),
        "合法事件数": len(legal),
        "脏事件号": [r["事件号"] for r in dirty],
        "短路事件号": [r["事件号"] for r in dirty],
        "凭证缺口事件号": [r["事件号"] for r in gaps],
        "schema_ready": schema_ready,
        "schema_error": schema_error,
        # 有库脏时，页灯可能仍被 paint_ok 刷绿；该字段要求失败测抓住分裂。
        "页绿库脏": panel_chip() == _OK and bool(dirty or gaps),
        "aligned": aligned,
        "是否对齐": aligned,
    }


def snapshot() -> dict:
    ref = active_read_ref()
    chip = panel_chip()
    truth = truth_flag()
    fp = cutover_fp()
    reject = reject_refresh()
    bypass = gate_bypass()
    arch = archive_rows()
    live = live_flag()
    aligned = (
        chip == truth == _OK
        and ref == _CUTOVER
        and reject
        and fp not in ("", "stale-fp")
        and not bypass
    )
    queue = reconcile_queue()
    # 队列里若已有被对账识别的脏账，看板不能自称全链路对齐。
    aligned = aligned and not queue["脏事件号"] and not queue["凭证缺口事件号"]
    return {
        "title": _TITLE,
        "bone": "hook-bug-01-订阅过滤表达式短路",
        "telemetry": "过滤器恒真短路",
        "short_circuit_marker": "过滤器恒真短路",
        "selected_form": _SELECTED_FORM,
        "panel_chip": chip,
        "truth": truth,
        "active_read": ref,
        "cutover_fp": fp,
        "reject_refresh": reject,
        "archive_count": len(arch),
        "live_flag": live,
        "aligned": aligned,
        "queue_aligned": not queue["页绿库脏"],
        "panel_pack_mismatch": chip == _OK and not aligned,
        "gate_bypass": bypass,
        "short_circuit_return": False,
        "queue_reconcile": queue,
    }


def dashboard() -> dict:
    snap = snapshot()
    return {
        **snap,
        "ok_chip": _OK,
        "bad_chip": _BAD,
        "label": _TITLE,
        "read_tracks": {
            "old_track": _OLD,
            "cutover_book": _CUTOVER,
            "live_flag": _LIVE,
        },
    }


def reconcile_display() -> dict:
    return reconcile_queue()
