"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

除了存数据，仓库还维护每个模块的「修订号」（revision）：任何会改变引用解析结果的
写入（登记、状态流转、字段更新）都会让修订号 +1。依赖解析缓存靠修订号判断快照是否
还有效——指标废弃、维度版本变化后旧缓存自然失效，而不是靠延长过期时间续命。
"""
from __future__ import annotations

from typing import Any

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        # 每个模块从 1 起步：0 只用来表示「从未读过」，避免与真实修订号混淆。
        self._revisions: dict[str, int] = {name: 1 for name in self._tables}

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def revision(self, module: str) -> int:
        """读取某模块当前修订号；模块不存在时登记为初始修订号 1。"""
        return self._revisions.setdefault(module, 1)

    def revisions(self, modules: list[str]) -> dict[str, int]:
        """一次性取多个模块的修订号，组成依赖快照的版本指纹。"""
        return {module: self.revision(module) for module in modules}

    def bump_revision(self, module: str) -> int:
        """写入路径调用：声明该模块数据已变化，所有按旧修订号缓存的结果即刻失效。"""
        next_revision = self._revisions.get(module, 1) + 1
        self._revisions[module] = next_revision
        return next_revision

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
