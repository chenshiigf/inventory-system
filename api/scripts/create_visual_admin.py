"""Create a LOCAL test htpasswd using a hidden prompt; never overwrite an existing file."""
import argparse
import getpass
from pathlib import Path

import bcrypt


def main():
    parser = argparse.ArgumentParser(description="创建本地测试管理员凭据（不是生产密码文件）")
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--user", default="admin")
    args = parser.parse_args()
    if not args.user or any(c in args.user for c in ":\r\n"):
        parser.error("用户名不能包含冒号或换行")
    if args.file.exists():
        parser.error("文件已存在，拒绝覆盖")
    password = getpass.getpass("本地测试密码：")
    confirm = getpass.getpass("再次输入：")
    if not password or password != confirm or len(password.encode()) > 72:
        parser.error("密码不一致、为空或超过 72 字节")
    args.file.parent.mkdir(parents=True, exist_ok=True)
    with args.file.open("x", encoding="utf8") as file:
        file.write(args.user + ":" + bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode() + "\n")
    print("已创建本地凭据文件；不要提交或复制到生产环境。")


if __name__ == "__main__":
    main()
