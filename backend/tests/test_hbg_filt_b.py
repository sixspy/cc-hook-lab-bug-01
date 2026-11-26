"""乙：事件队列对账不得把“过滤器恒真短路”的脏行认成合法入队。

测试显式写入夹具键与库态：一条旧口径短路放行的待投事件，以及
``filt_short_circuit_<事件号>`` 标记。当前互证/对账只读事件状态，
会把它计入总数和待投，甚至返回对齐，因此本文件必须先红。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="hbg_filt_b_"))
os.environ["数据目录"] = str(_TMP)
os.environ["夹具目录"] = "/workspace/fixtures"

from app import db, seed  # noqa: E402

db.数据目录 = _TMP
db.库路径 = _TMP / "hbg_filt_b.db"

from app.services import 互证, 签名目录, filt as filt_svc  # noqa: E402

EID = "EVT-HBG-FILT-B-DIRTY"
FIXTURE_KEY = "hbg_filt_b_fixture"
SHORT_KEY = f"filt_short_circuit_{EID}"


def setup_module() -> None:
    # 全量 pytest 收集时后续模块可能改写 db.库路径；每个模块开跑前重新钉回自己的库。
    db.数据目录 = _TMP
    db.库路径 = _TMP / "hbg_filt_b.db"
    seed.初始化库()
    t = "2026-11-26T00:00:00Z"
    conn = db.连接()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO 事件"
            "(事件号,事件类型,端点键,载荷摘要,优先级,状态,入队时,更新时) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (EID, "hbg.filt.b.dirty", "ep-billing", "dirty-short-true", "普通", "待投", t, t),
        )
        conn.execute(
            "INSERT INTO 投递元(键,值) VALUES (?,?) "
            "ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
            (
                FIXTURE_KEY,
                json.dumps(
                    {
                        "bone": "hook-bug-01-订阅过滤表达式短路",
                        "eid": EID,
                        "state": "恒真短路",
                        "expect": "对账不得计为合法入队",
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        conn.execute(
            "INSERT INTO 投递元(键,值) VALUES (?,?) "
            "ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
            (SHORT_KEY, "过滤器恒真短路"),
        )
        conn.execute(
            "INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
            (t, "入队", EID, "过滤器恒真短路：解析异常吞掉后返回真"),
        )
        conn.commit()
    finally:
        conn.close()


def test_对账识别短路脏账且不称对齐() -> None:
    rep = 互证.跑一次()

    assert rep.get("过滤短路放行数", 0) >= 1
    assert rep.get("合法过滤凭证缺口", 0) >= 1
    assert rep.get("是否对齐") is False
    assert rep.get("是否全部对齐") is False

    账实 = rep.get("账实") or {}
    assert 账实.get("短路放行") == rep["过滤短路放行数"]
    assert EID in (账实.get("短路事件号") or [])


def test_现行队列合法读集排除短路行_页仍可展示库脏() -> None:
    legal = 签名目录.合法事件列表()
    assert all(row["事件号"] != EID for row in legal)

    all_rows = 签名目录.事件列表()
    dirty_row = next(row for row in all_rows if row["事件号"] == EID)
    assert dirty_row["状态"] == "待投"  # 库脏仍在，失败测必须能看见，而不是悄悄抹行。
    assert dirty_row.get("合法入账") is False
    assert dirty_row.get("账实标记") in {"过滤器恒真短路", "short_circuit_true", "脏账"}

    audit = filt_svc.reconcile_queue()
    assert audit.get("短路放行合法数", 1) == 0
    assert audit.get("页绿库脏") is True
    assert EID in audit.get("脏事件号", [])
