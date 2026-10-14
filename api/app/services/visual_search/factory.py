"""Only fixed local sources. Production index cannot target existing dev/phase15 files."""
from dataclasses import replace
import json
from pathlib import Path
import threading

from .images import VisualError
from .production_catalog import atomic_json
from .service import VisualService
from .sources import ProductionSource
from .runtime_control import DailyBudgetLedger, RuntimeSwitch


def production_service(visual_root, config, *, catalog_root=None, provider=None, initialize=False,
                       runtime_switch=None, budget=None):
    visual_root = Path(visual_root).resolve()
    source = ProductionSource(catalog_root or visual_root / "production-catalog")
    root = visual_root / "production-indices" / source.identity_key
    if not root.resolve().is_relative_to(visual_root) or root.is_symlink():
        raise VisualError("生产索引路径越界", 503)
    binding = {"format": "production-visual-index-v1", "source_id": source.source_id,
               "source_identity": source.mirror.identity, "index_namespace": source.identity_key}
    marker = root / "source-binding.json"
    if root.exists():
        for name in ("source-binding.json", "index.db", "calls.lock", "jobs", "queries"):
            path = root / name
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or (path.is_file() and path.stat().st_nlink > 1):
                raise VisualError("生产索引包含链接，拒绝使用", 503)
        if marker.exists():
            if json.loads(marker.read_text(encoding="utf8")) != binding:
                raise VisualError("生产索引来源不一致，拒绝使用", 503)
        elif any(root.iterdir()):
            raise VisualError("非空目录不是生产索引，拒绝覆盖", 503)
    if initialize:
        from alembic import command
        from alembic.config import Config
        root.mkdir(parents=True, exist_ok=True)
        if not marker.exists():
            atomic_json(marker, binding)
        migration = Config(str(Path(__file__).resolve().parents[3] / "visual-alembic.ini"))
        migration.set_main_option("sqlalchemy.url", f"sqlite:///{(root / 'index.db').as_posix()}")
        command.upgrade(migration, "vs02")
    return VisualService(root, None, replace(config, source_identity=source.identity_key), provider,
                         source=source, call_lock_root=visual_root,
                         runtime_switch=runtime_switch or RuntimeSwitch(visual_root),
                         budget=budget or DailyBudgetLedger(Path(visual_root) / "daily-budget.sqlite3", config))


class VisualSources:
    def __init__(self, development, root, config, *, catalog_root=None, provider=None,
                 runtime_switch=None, budget=None):
        self.development, self.root, self.config = development, root, config
        self.catalog_root, self.provider = catalog_root, provider
        self.runtime_switch, self.budget = runtime_switch, budget
        self.production = None
        self.lock = threading.Lock()

    def get(self, name):
        if name == "development":
            return self.development
        if name != "production-catalog":
            raise VisualError("未知图库数据源", 422)
        with self.lock:
            if self.production is None:
                self.production = production_service(self.root, self.config, catalog_root=self.catalog_root,
                                                    provider=self.provider, runtime_switch=self.runtime_switch,
                                                    budget=self.budget)
                self.production.slot = self.development.slot
            return self.production

    def close(self):
        if self.production:
            self.production.close()
