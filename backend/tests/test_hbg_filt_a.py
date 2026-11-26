"""甲：非法订阅过滤器不得恒真放行。

骨签：hook-bug-01-订阅过滤表达式短路
复现：写入 ``raise ...`` 非法表达式，再灌必然不匹配事件。
当前主干会吞掉解析异常并把事件写成“待投”，因此本文件必须先红。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="hbg_filt_a_"))
os.environ["数据目录"] = str(_TMP)
os.environ["夹具目录"] = "/workspace/fixtures"

from app import db, seed  # noqa: E402

db.数据目录 = _TMP
db.库路径 = _TMP / "hbg_filt_a.db"

import pytest  # noqa: E402

from app.factory import create_app  # noqa: E402
from app.services import 入队, 端点  # noqa: E402

KEY = "ep-hbg-filt-a"
EID = "EVT-HBG-FILT-A-BAD"
BAD_EXPR = "raise ValueError('hbg_filt_a_illegal_expression')"
EVENT_TYPE = "hbg.filt.a.definitely_not_matched"


def setup_module() -> None:
    # 全量 pytest 收集时后续模块可能改写 db.库路径；每个模块开跑前重新钉回自己的库。
    db.数据目录 = _TMP
    db.库路径 = _TMP / "hbg_filt_a.db"
    seed.初始化库()


def _write_bad_endpoint() -> None:
    端点.登记(
        {
            "端点键": KEY,
            "名称": "甲非法过滤器端点",
            "回调URL": "https://hook.example.local/hbg-a",
            "密钥版本": "v1",
            # 旧实现会把“说明”也当过滤器表达式读取。
            "说明": BAD_EXPR,
            "过滤表达式": BAD_EXPR,
        }
    )


def test_非法表达式不把不匹配事件写成待投() -> None:
    _write_bad_endpoint()

    # 正确口径是 fail-closed：解析失败必须拒绝入队，而不是 except: pass 后返回真。
    with pytest.raises(ValueError, match="过滤器|表达式|解析失败"):
        入队.建事件(
            {
                "事件号": EID,
                "事件类型": EVENT_TYPE,
                "端点键": KEY,
                "载荷摘要": "match=impossible",
                "过滤器": BAD_EXPR,
            }
        )

    conn = db.连接()
    try:
        pending = conn.execute(
            "SELECT 状态, COUNT(*) AS c FROM 事件 WHERE 事件号=? GROUP BY 状态",
            (EID,),
        ).fetchall()
        assert not any(r["状态"] == "待投" and r["c"] > 0 for r in pending)

        cred = conn.execute(
            "SELECT * FROM 过滤凭证 WHERE 事件号=? AND 决定='拒收' "
            "ORDER BY rowid DESC LIMIT 1",
            (EID,),
        ).fetchone()
        assert cred is not None, "解析失败必须留下独立拒收凭证，不能静默消失"
        assert cred["指纹"] and cred["指纹"] != "stale-fp"
        assert "cutover" in str(cred["口径"]) or "凭证" in str(cred["口径"])
    finally:
        conn.close()


def test_端点服务假绿与短路标记必须翻真() -> None:
    badge = 端点.过滤器徽标(KEY)
    assert badge["端点键"] == KEY
    assert badge.get("短路放行") is False
    assert badge.get("恒真短路") is False
    assert badge.get("解析状态") == "解析失败"
    assert badge.get("过滤已生效") is False
    assert badge.get("指纹") and badge["指纹"] != "stale-fp"


def test_http_入队口与事件队列页口径一致() -> None:
    app = create_app()
    client = app.test_client()
    assert client.post("/api/登录", json={"账号": "admin", "密码": "123456"}).status_code == 200

    key = "ep-hbg-filt-a-http"
    eid = "EVT-HBG-FILT-A-HTTP-BAD"
    r = client.post(
        "/api/端点",
        json={
            "端点键": key,
            "名称": "甲HTTP",
            "回调URL": "https://hook.example.local/hbg-a-http",
            "说明": BAD_EXPR,
            "过滤表达式": BAD_EXPR,
        },
    )
    assert r.status_code == 200

    blocked = client.post(
        "/api/事件/入队",
        json={
            "事件号": eid,
            "事件类型": EVENT_TYPE,
            "端点键": key,
            "载荷摘要": "http=not-match",
        },
    )
    assert blocked.status_code == 400

    rows = client.get("/api/事件").get_json()
    assert all(row["事件号"] != eid or row["状态"] != "待投" for row in rows)

    badge = client.get(f"/api/端点/过滤器?端点键={key}")
    assert badge.status_code == 200
    body = badge.get_json()
    assert body["短路放行"] is False
    assert body["解析状态"] == "解析失败"
