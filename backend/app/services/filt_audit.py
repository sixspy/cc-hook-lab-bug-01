"""订阅过滤读轨审计。"""
from __future__ import annotations

from app.services import filt as core
from app.services import filt_cutover as cutover
from app.services import filt_dualwrite as dual


def audit_summary() -> dict:
    return {
        "title": "订阅过滤审计",
        "core": core.dashboard(),
        "cutover": cutover.board(),
        "dualwrite": dual.dual_board(),
        "half": core.half_fix_display(),
    }
