"""业务模块路由汇总。

这里统一按别名导入再暴露 ROUTERS：模块名有可能和内置名撞车（某个业务模块就叫 dict、list
这种名字时），按名字直接 import 会把内置类型覆盖掉，函数注解在运行时求值就会报
'module' object is not subscriptable。
"""
from __future__ import annotations

from app.routers import landing as router_landing
from app.routers import ods as router_ods
from app.routers import dwd as router_dwd
from app.routers import dim as router_dim
from app.routers import dws as router_dws
from app.routers import ads as router_ads
from app.routers import partition as router_partition
from app.routers import quality as router_quality
from app.routers import lineage as router_lineage
from app.routers import backfill as router_backfill
from app.routers import cleanrule as router_cleanrule
from app.routers import transform as router_transform
from app.routers import dictionary as router_dictionary
from app.routers import metamodel as router_metamodel
from app.routers import lifecycle as router_lifecycle
from app.routers import cost as router_cost
from app.routers import sync as router_sync
from app.routers import auditlog as router_auditlog
from app.routers import revaluation as router_revaluation

ROUTERS = [router_landing, router_ods, router_dwd, router_dim, router_dws, router_ads, router_partition, router_quality, router_lineage, router_backfill, router_cleanrule, router_transform, router_dictionary, router_metamodel, router_lifecycle, router_cost, router_sync, router_auditlog, router_revaluation]
