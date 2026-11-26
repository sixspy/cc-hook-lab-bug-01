"""双写两轨备选形态。

本缺陷修复择定“独立凭证钉过滤器指纹并拒恒真短路回刷”，因此双写轨只能作为
未启用的备选说明存在：不得写 live 旗、不得建第二套现行读集，更不能与
cutover_book 同时生效。
"""
from __future__ import annotations

from app.services import filt as core

_TITLE = "订阅过滤"


class DualWriteFormDisabled(ValueError):
    """已择定 cutover_book，禁止并存启用双写第二读轨。"""


def apply_dualwrite(note: str = "") -> dict:
    raise DualWriteFormDisabled(
        "工程形态已择定为独立凭证切流；双写归档/live_flag 读轨禁止并存"
    )


def dual_board() -> dict:
    dash = core.dashboard()
    return {
        "dual_chip": "双写轨未启用",
        "enabled": False,
        "selected": False,
        "coexistence_rejected": True,
        "truth": core.truth_flag(),
        "active_read": core.active_read_ref(),
        "selected_form": "cutover_book",
        "archive": [],
        "archive_count": 0,
        "live_flag": "",
        "live_read_ref": "",
        "dash": dash,
        "fate": "dualwrite_not_selected",
        "panel_pack_mismatch": False,
        "title": f"{_TITLE}双写备选面（未启用）",
        "note": "两套读引用不得并存；当前唯一现行读源为 cutover_book 独立凭证",
    }
