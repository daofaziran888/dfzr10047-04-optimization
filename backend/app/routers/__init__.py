"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import deviation_repair, resource

# 偏差修复的固定子路径必须先于 /api/{module} 动态路由注册，
# 否则 /api/deviation_repair/{claim}/detail 会被当成条目编号解析。
ROUTERS = [deviation_repair, resource]
