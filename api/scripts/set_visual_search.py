"""Enable or disable AI search through its private runtime marker; no API restart."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.data_paths import resolve_inventory_data_dir
from app.services.visual_search.config import visual_directory
from app.services.visual_search.runtime_control import RuntimeSwitch


def main() -> int:
    parser = argparse.ArgumentParser(description="切换 AI 找货运行状态；仅限可信服务器/本机操作员")
    parser.add_argument("action", choices=("enable", "disable"))
    parser.add_argument("--data-dir", type=Path, help="与 FastAPI INVENTORY_DATA_DIR 一致的数据根目录")
    args = parser.parse_args()
    data_root = args.data_dir.resolve() if args.data_dir else resolve_inventory_data_dir()
    switch = RuntimeSwitch(visual_directory(data_root))
    switch.set_enabled(args.action == "enable")
    print("AI找货已启用" if args.action == "enable" else "AI找货已关闭")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
