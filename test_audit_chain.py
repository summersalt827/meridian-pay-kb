#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""审计哈希链: append-only 触发器 + 链重算验证 (FinTech 能力⑥ 防篡改)。

离线套件, 不依赖服务进程: 直接用 AuditLog 构造临时库。
"""
from __future__ import annotations

import os
import sqlite3
import tempfile

import kb_qa

failed: list[str] = []
passed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global passed
    if cond:
        passed += 1
        print(f"  ✅ {name}")
    else:
        failed.append(name)
        print(f"  ❌ {name}: {detail}")


def _user() -> kb_qa.User:
    return kb_qa.User(username="alice", role="admin")


def _tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def main() -> int:
    # 1) 追加 + 验证闭环
    path = _tmp_db()
    audit = kb_qa.AuditLog(path)
    for q in ["问题一", "问题二", "问题三"]:
        audit.log(_user(), q, [], "无命中", "extractive")
    v = audit.verify()
    check("追加 3 条后链完整", v["ok"] and v["rows"] == 3, str(v))

    # 2) 空库视为完整
    empty = kb_qa.AuditLog(_tmp_db())
    v = empty.verify()
    check("空库链完整", v["ok"] and v["rows"] == 0, str(v))

    # 3) 触发器拦截 UPDATE
    conn = sqlite3.connect(path)
    try:
        conn.execute("UPDATE audit_log SET question='篡改' WHERE id=2")
        check("触发器拦截 UPDATE", False, "UPDATE 未被拦截")
    except sqlite3.IntegrityError:
        check("触发器拦截 UPDATE", True)
    conn.close()

    # 4) 触发器拦截 DELETE
    conn = sqlite3.connect(path)
    try:
        conn.execute("DELETE FROM audit_log WHERE id=2")
        check("触发器拦截 DELETE", False, "DELETE 未被拦截")
    except sqlite3.IntegrityError:
        check("触发器拦截 DELETE", True)
    conn.close()

    # 5) 绕过触发器篡改 → 链重算定位断点
    conn = sqlite3.connect(path)
    conn.execute("DROP TRIGGER audit_no_update")
    conn.execute("UPDATE audit_log SET question='篡改' WHERE id=2")
    conn.commit()
    conn.close()
    v = audit.verify()
    check("篡改被链重算定位", (not v["ok"]) and v["broken_at"] == 2, str(v))

    # 6) 旧库迁移: 无 chain_hash 的历史行回填后可验证
    legacy = _tmp_db()
    conn = sqlite3.connect(legacy)
    conn.execute("""
        CREATE TABLE audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL, user TEXT NOT NULL, role TEXT NOT NULL,
            question TEXT NOT NULL, source_docs TEXT NOT NULL,
            answer TEXT, answer_mode TEXT
        )
    """)
    conn.execute(
        "INSERT INTO audit_log(ts, user, role, question, source_docs, answer, answer_mode) "
        "VALUES('2026-10-13T20:00:00+08:00','sam','sales','旧问题','[]','旧回答','extractive')")
    conn.execute(
        "INSERT INTO audit_log(ts, user, role, question, source_docs, answer, answer_mode) "
        "VALUES('2026-10-13T20:01:00+08:00','sam','sales','旧问题二','[]','旧回答二','extractive')")
    conn.commit()
    conn.close()
    migrated = kb_qa.AuditLog(legacy)
    v = migrated.verify()
    check("旧库迁移回填后可验证", v["ok"] and v["rows"] == 2, str(v))

    for p in (path, legacy):
        try:
            os.remove(p)
        except OSError:
            pass

    print(f"\n审计哈希链: {passed}/{passed + len(failed)} 通过")
    if failed:
        print("失败: " + ", ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
