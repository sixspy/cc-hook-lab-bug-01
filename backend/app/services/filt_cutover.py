"""订阅过滤切流账册：择定独立凭证形态，锁真实指纹并拒回刷。"""
from __future__ import annotations

from app.db import 连接
from app.services import filt as core
from app.services import 过滤

_SLUG = "filt"
_TITLE = "订阅过滤"
_OK = "过滤已亮"


def append_cutover(ref_id: int, note: str = "", expression: str = "") -> dict:
    expression = expression or note or f"cutover-ref-{ref_id}-hook-bug-01"
    fp = 过滤.指纹表达式(expression)
    conn = 连接()
    try:
        core._ensure(conn)
        core._set(conn, f"{_SLUG}_cutover_note_{ref_id}", note or _TITLE)
        core._set(conn, f"{_SLUG}_panel_chip", _OK)
        core._set(conn, f"{_SLUG}_truth", _OK)
        core._set(conn, f"{_SLUG}_active_read", core._CUTOVER)
        core._set(conn, f"{_SLUG}_cutover_fp", fp)
        core._set(conn, f"{_SLUG}_reject_refresh", "1")
        core._set(conn, f"{_SLUG}_gate_bypass", "0")
        core._set(conn, f"{_SLUG}_mode", core._CUTOVER)
        core._set(conn, f"{_SLUG}_selected_form", core._CUTOVER)
        core._set(conn, f"{_SLUG}_live_flag", "")
        core._write_global_credential(
            conn,
            "钉版",
            fp,
            f"切流凭证 ref_id={ref_id}；拒绝恒真短路回刷",
            expression,
        )
        conn.commit()
    finally:
        conn.close()
    return {
        "ok": True,
        "ref_id": ref_id,
        "note": note,
        "fate": "cutover_book",
        "selected_form": "cutover_book",
        "panel": core.panel_chip(),
        "truth": core.truth_flag(),
        "active_read": core.active_read_ref(),
        "cutover_fp": core.cutover_fp(),
        "reject_refresh": core.reject_refresh(),
        "gate_bypass": core.gate_bypass(),
    }


def board() -> dict:
    snap = core.dashboard()
    snap["fate"] = "cutover_book"
    snap["selected_form"] = "cutover_book"
    # 不再硬编码 mismatch；由快照根据页灯/库脏/指纹计算。
    return snap
