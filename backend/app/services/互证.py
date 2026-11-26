from __future__ import annotations

from app.db import 连接
from app.services.时间 import 此刻
from app.services import 签名目录


def 跑一次() -> dict:
    t = 此刻()
    conn = 连接()
    try:
        总 = conn.execute("SELECT COUNT(*) AS c FROM 事件").fetchone()["c"]
        已 = conn.execute("SELECT COUNT(*) AS c FROM 事件 WHERE 状态='已投递'").fetchone()["c"]
        死 = conn.execute("SELECT COUNT(*) AS c FROM 事件 WHERE 状态='死信'").fetchone()["c"]
        待 = conn.execute("SELECT COUNT(*) AS c FROM 事件 WHERE 状态 IN ('待投','投递中')").fetchone()["c"]
        匣 = conn.execute("SELECT COUNT(*) AS c FROM 死信 WHERE 状态='待处置'").fetchone()["c"]

        rows = 签名目录.事件列表()
        short_rows = [r for r in rows if r.get("账实标记") == "过滤器恒真短路"]
        gap_rows = [
            r for r in rows
            if r.get("合法入账") is False
        ]
        legal_rows = [r for r in rows if r.get("合法入账") is True]
        short_ids = [r["事件号"] for r in short_rows]
        gap_ids = [r["事件号"] for r in gap_rows]
        short_pending = sum(1 for r in short_rows if r.get("状态") in {"待投", "投递中"})
        legal_pending = sum(1 for r in legal_rows if r.get("状态") in {"待投", "投递中"})

        状态口径对齐 = (死 == 匣 and 已 + 死 + 待 <= 总)
        过滤口径对齐 = not short_rows and not gap_rows
        对齐 = 1 if (状态口径对齐 and 过滤口径对齐) else 0
        if not 过滤口径对齐:
            说明 = "发现过滤器恒真短路脏账或缺指纹凭证，不得计为合法入队"
        elif not 状态口径对齐:
            说明 = "死信匣与事件死信数不一致或水位异常"
        else:
            说明 = "事件/投递/死信/过滤凭证口径对齐"

        conn.execute(
            "INSERT INTO 互证批次(时刻,事件总数,已投递数,死信数,待投数,是否对齐,说明) VALUES (?,?,?,?,?,?,?)",
            (t, 总, 已, 死, 待, 对齐, 说明),
        )
        conn.commit()
        return {
            "时刻": t,
            "事件总数": 总,
            "已投递数": 已,
            "死信数": 死,
            "待投数": legal_pending,
            "账面待投数": 待,
            "死信匣待处置": 匣,
            "过滤短路放行数": len(short_rows),
            "过滤短路待投数": short_pending,
            "合法过滤凭证缺口": len(gap_rows),
            "是否对齐": bool(对齐),
            "是否全部对齐": bool(对齐),
            "说明": 说明,
            "账实": {
                "总": 总,
                "已": 已,
                "死": 死,
                "待": legal_pending,
                "账面待": 待,
                "匣": 匣,
                "合法事件": len(legal_rows),
                "短路放行": len(short_rows),
                "短路事件号": short_ids,
                "凭证缺口事件号": gap_ids,
            },
            "明细": {
                "总": 总,
                "已": 已,
                "死": 死,
                "待": legal_pending,
                "账面待": 待,
                "匣": 匣,
                "短路": len(short_rows),
                "凭证缺口": len(gap_rows),
            },
        }
    finally:
        conn.close()
