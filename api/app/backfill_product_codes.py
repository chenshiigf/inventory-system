def main() -> None:
    # Never import the default database or try to repair a missing V2 migration.
    raise SystemExit("旧三段式编号回填命令已停用；历史换号需要独立预演和授权。")


if __name__ == "__main__":
    main()
