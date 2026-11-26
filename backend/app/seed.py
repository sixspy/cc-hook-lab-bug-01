from datetime import datetime, timezone
from app.db import 连接
from app.services import 过滤

建表 = """
CREATE TABLE IF NOT EXISTS 用户 (
  账号 TEXT PRIMARY KEY,
  密码哈希 TEXT NOT NULL,
  角色 TEXT NOT NULL DEFAULT '管理员'
);
CREATE TABLE IF NOT EXISTS 签名规 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 投递端点 (
  端点键 TEXT PRIMARY KEY,
  名称 TEXT NOT NULL,
  回调URL TEXT NOT NULL,
  密钥版本 TEXT NOT NULL DEFAULT 'v1',
  启用 INTEGER NOT NULL DEFAULT 1,
  说明 TEXT NOT NULL DEFAULT '',
  过滤表达式 TEXT NOT NULL DEFAULT '',
  过滤指纹 TEXT NOT NULL DEFAULT '',
  过滤态 TEXT NOT NULL DEFAULT '未设置',
  过滤版本 INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS 事件 (
  事件号 TEXT PRIMARY KEY,
  事件类型 TEXT NOT NULL,
  端点键 TEXT NOT NULL DEFAULT '',
  载荷摘要 TEXT NOT NULL DEFAULT '',
  优先级 TEXT NOT NULL DEFAULT '普通',
  状态 TEXT NOT NULL DEFAULT '待投',
  入队时 TEXT NOT NULL,
  更新时 TEXT NOT NULL,
  过滤指纹 TEXT NOT NULL DEFAULT '',
  过滤口径 TEXT NOT NULL DEFAULT '',
  过滤状态 TEXT NOT NULL DEFAULT '未校验'
);
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
);
CREATE INDEX IF NOT EXISTS idx_过滤凭证_事件 ON 过滤凭证(事件号);
CREATE INDEX IF NOT EXISTS idx_过滤凭证_端点 ON 过滤凭证(端点键);
CREATE TABLE IF NOT EXISTS 投递尝试 (
  尝试号 TEXT PRIMARY KEY,
  事件号 TEXT NOT NULL,
  端点键 TEXT NOT NULL,
  签名摘要 TEXT NOT NULL DEFAULT '',
  状态 TEXT NOT NULL DEFAULT '失败',
  响应码 INTEGER NOT NULL DEFAULT 0,
  说明 TEXT NOT NULL DEFAULT '',
  投递时 TEXT NOT NULL,
  FOREIGN KEY (事件号) REFERENCES 事件(事件号),
  FOREIGN KEY (端点键) REFERENCES 投递端点(端点键)
);
CREATE TABLE IF NOT EXISTS 死信 (
  死信号 TEXT PRIMARY KEY,
  事件号 TEXT NOT NULL UNIQUE,
  端点键 TEXT NOT NULL,
  失败次数 INTEGER NOT NULL DEFAULT 0,
  末次原因 TEXT NOT NULL DEFAULT '',
  状态 TEXT NOT NULL DEFAULT '待处置',
  入匣时 TEXT NOT NULL,
  FOREIGN KEY (事件号) REFERENCES 事件(事件号)
);
CREATE TABLE IF NOT EXISTS 互证批次 (
  批次号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  事件总数 INTEGER NOT NULL,
  已投递数 INTEGER NOT NULL,
  死信数 INTEGER NOT NULL,
  待投数 INTEGER NOT NULL,
  是否对齐 INTEGER NOT NULL,
  说明 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 投递元 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 操作留痕 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  操作 TEXT NOT NULL,
  事件号 TEXT NOT NULL DEFAULT '',
  详情 TEXT NOT NULL DEFAULT ''
);
"""


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def 初始化库() -> None:
    conn = 连接()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
    except Exception:
        # 某些受限文件系统不支持 WAL；互斥文件锁仍会串行化关键写路径。
        pass
    conn.executescript(建表)
    _迁移旧库(conn)
    _seed_filt_chip(conn)
    if conn.execute("SELECT COUNT(*) AS c FROM 用户").fetchone()["c"] == 0:
        conn.execute("INSERT INTO 用户(账号,密码哈希,角色) VALUES (?,?,?)", ("admin", "123456", "管理员"))
    if conn.execute("SELECT COUNT(*) AS c FROM 签名规").fetchone()["c"] == 0:
        for k, v in {
            "最大重试": "3",
            "产品名": "投递台",
            "主链": "端点登记→事件入队→签名投递→失败重试与死信→投递账互证",
            "签名算法": "HMAC-SHA256",
            "超时秒": "5",
        }.items():
            conn.execute("INSERT INTO 签名规(键,值) VALUES (?,?)", (k, v))
    if conn.execute("SELECT COUNT(*) AS c FROM 投递端点").fetchone()["c"] == 0:
        for row in [
            ("ep-billing", "账单回调", "https://example.local/hooks/billing", "v1", 1, "账务"),
            ("ep-notify", "通知回调", "https://example.local/hooks/notify", "v1", 1, "通知"),
            ("ep-audit", "审计回调", "https://example.local/hooks/audit", "v2", 1, "审计"),
            ("ep-legacy", "旧版回调", "https://example.local/hooks/legacy", "v1", 0, "停用"),
            ("ep-partner", "合作方回调", "https://partner.local/ingest", "v1", 1, "外部"),
        ]:
            conn.execute(
                "INSERT INTO 投递端点(端点键,名称,回调URL,密钥版本,启用,说明) VALUES (?,?,?,?,?,?)",
                row,
            )
    if conn.execute("SELECT COUNT(*) AS c FROM 事件").fetchone()["c"] == 0:
        t = 此刻()
        for row in [
            ("EVT-demo-01", "invoice.paid", "ep-billing", "invoice=1001", "普通", "待投"),
            ("EVT-demo-02", "user.created", "ep-notify", "user=u42", "普通", "投递中"),
            ("EVT-demo-03", "order.refund", "ep-audit", "order=908", "紧急", "待投"),
            ("EVT-demo-04", "user.updated", "ep-notify", "user=u7", "低", "已投递"),
            ("EVT-demo-05", "invoice.failed", "ep-billing", "invoice=1002", "普通", "死信"),
        ]:
            conn.execute(
                "INSERT INTO 事件(事件号,事件类型,端点键,载荷摘要,优先级,状态,入队时,更新时) VALUES (?,?,?,?,?,?,?,?)",
                (*row, t, t),
            )
        conn.execute(
            "INSERT INTO 投递尝试(尝试号,事件号,端点键,签名摘要,状态,响应码,说明,投递时) VALUES (?,?,?,?,?,?,?,?)",
            ("TRY-demo-01", "EVT-demo-04", "ep-notify", "sig-demo", "成功", 200, "ok", t),
        )
        conn.execute(
            "INSERT INTO 死信(死信号,事件号,端点键,失败次数,末次原因,状态,入匣时) VALUES (?,?,?,?,?,?,?)",
            ("DL-demo-01", "EVT-demo-05", "ep-billing", 3, "连接超时", "待处置", t),
        )
        conn.execute(
            "INSERT INTO 操作留痕(时刻,操作,事件号,详情) VALUES (?,?,?,?)",
            (t, "种子", "", "初始化投递台样例"),
        )
    # 内置样例明确标为合法；运行期脏行不得由每次启动的迁移统一洗白。
    conn.execute(
        "UPDATE 事件 SET 过滤口径='seed_legal', 过滤状态='内置种子' "
        "WHERE 事件号 LIKE 'EVT-demo-%' AND COALESCE(过滤口径,'')=''"
    )
    conn.commit()
    conn.close()


def _迁移旧库(conn) -> None:
    needed = {
        "投递端点": {
            "过滤表达式": "TEXT NOT NULL DEFAULT ''",
            "过滤指纹": "TEXT NOT NULL DEFAULT ''",
            "过滤态": "TEXT NOT NULL DEFAULT '未设置'",
            "过滤版本": "INTEGER NOT NULL DEFAULT 0",
        },
        "事件": {
            "过滤指纹": "TEXT NOT NULL DEFAULT ''",
            "过滤口径": "TEXT NOT NULL DEFAULT ''",
            "过滤状态": "TEXT NOT NULL DEFAULT '未校验'",
        },
    }
    for table, columns in needed.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        added_event_read_track = table == "事件" and "过滤口径" not in existing
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        if added_event_read_track:
            # 仅“从旧版本迁移的存量行”给历史口径；新版本运行期写入的空口径
            # 表示缺凭证，不能在每次初始化时被冒充合法。
            conn.execute(
                "UPDATE 事件 SET 过滤口径='history_legal', 过滤状态='历史入账' "
                "WHERE COALESCE(过滤口径,'')=''"
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_过滤凭证_事件 ON 过滤凭证(事件号)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_过滤凭证_端点 ON 过滤凭证(端点键)")



def _seed_filt_chip(conn) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS 投递元(键 TEXT PRIMARY KEY, 值 TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO 投递元(键,值) VALUES (?,?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
        ("filt_panel_chip", "过滤已亮"),
    )
    conn.execute(
        "INSERT INTO 投递元(键,值) VALUES (?,?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
        ("filt_truth", "过滤已亮"),
    )
    conn.execute(
        "INSERT INTO 投递元(键,值) VALUES (?,?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
        ("filt_active_read", "cutover_book"),
    )
    policy_fp = 过滤.指纹表达式(
        "independent-credential-cutover|hook-bug-01|filt"
    )
    fp_row = conn.execute(
        "SELECT 值 FROM 投递元 WHERE 键='filt_cutover_fp'"
    ).fetchone()
    current_fp = str(fp_row["值"] or "") if fp_row else ""
    if (
        not current_fp
        or current_fp in {"stale-fp", "old", "short-true", "fpf_seed_hbg01_cutover_credential"}
        or not current_fp.startswith("fpf_")
    ):
        conn.execute(
            "INSERT INTO 投递元(键,值) VALUES ('filt_cutover_fp',?) "
            "ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
            (policy_fp,),
        )
    conn.execute(
        "INSERT INTO 投递元(键,值) VALUES (?,?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
        ("filt_reject_refresh", "1"),
    )
    conn.execute(
        "INSERT INTO 投递元(键,值) VALUES (?,?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
        ("filt_gate_bypass", "0"),
    )
    conn.execute(
        "INSERT INTO 投递元(键,值) VALUES (?,?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
        ("filt_mode", "cutover_book"),
    )
    conn.execute(
        "INSERT INTO 投递元(键,值) VALUES (?,?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
        ("filt_selected_form", "cutover_book"),
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
    conn.execute(
        """
        INSERT INTO 过滤凭证
        (凭证号,端点键,事件号,版本,表达式,指纹,口径,决定,原因,时刻)
        VALUES (?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(凭证号) DO UPDATE SET
          指纹=excluded.指纹,
          口径=excluded.口径,
          决定=excluded.决定,
          原因=excluded.原因,
          时刻=excluded.时刻
        """,
        (
            "FPC-G-HBG01-POLICY",
            "_global_filt_cutover",
            "",
            1,
            "form=cutover_book;bone=hook-bug-01-订阅过滤表达式短路",
            policy_fp,
            "独立凭证切流/cutover_book",
            "钉版",
            "种子策略凭证：唯一保留独立凭证读轨，拒绝恒真短路回刷",
            此刻(),
        ),
    )
