import sqlite3
import time

c = sqlite3.connect(":memory:")
c.execute("CREATE VIRTUAL TABLE t USING fts5(x, tokenize='trigram')")
c.execute("CREATE TABLE l(id INTEGER PRIMARY KEY, x TEXT)")

rows = ["整理滨江法院立案材料", "写周报和月报", "买猫粮", "给鱼缸换水", "导出采集清单并上传需求单"]
for r in rows:
    c.execute("INSERT INTO t(x) VALUES(?)", (r,))
    c.execute("INSERT INTO l(x) VALUES(?)", (r,))

print("== trigram 按查询长度表现 ==")
for q in ["立案", "院立案", "滨江法院", "周报", "猫粮"]:
    try:
        n = c.execute("SELECT count(*) FROM t WHERE t MATCH ?", (f'"{q}"',)).fetchone()[0]
    except Exception as e:
        n = f"ERR {e}"
    like = c.execute("SELECT count(*) FROM l WHERE x LIKE ?", (f"%{q}%",)).fetchone()[0]
    print(f"  {q!r:12} len={len(q)}  trigram={n}  LIKE={like}")

# 规模测试：LIKE 在 5000 条上的耗时
c.executemany("INSERT INTO l(x) VALUES(?)", [(f"填充任务 {i} 立案材料",) for i in range(5000)])
t0 = time.perf_counter()
for _ in range(20):
    c.execute("SELECT id FROM l WHERE x LIKE '%立案%'").fetchall()
print(f"== LIKE 扫描 5002 行 ×20 次：{(time.perf_counter()-t0)*1000:.1f} ms ==")
