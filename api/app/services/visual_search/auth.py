"""Verify the same htpasswd credentials as the existing Nginx gateway."""
import base64
import hashlib
import hmac
from pathlib import Path

import bcrypt
from fastapi import HTTPException, Request
from passlib.hash import apr_md5_crypt


def require_visual_user(request: Request) -> str:
    from .config import VisualConfig
    if getattr(request.app.state, "visual_config_error", False):
        raise HTTPException(503, "找货服务配置暂不可用，请联系管理员")
    config = getattr(request.app.state, "visual_config", None) or VisualConfig.from_env()
    if not config.htpasswd_file:
        raise HTTPException(503, "找货用户鉴权尚未配置，请联系管理员")
    if request.method == "POST":
        origin = request.headers.get("Origin")
        same_origin = f"{request.url.scheme}://{request.url.netloc}"
        if (origin and origin not in {*config.allowed_origins, same_origin}) or request.headers.get("Sec-Fetch-Site") == "cross-site":
            raise HTTPException(403, "请从库存系统页面发起操作")
    try:
        scheme, value = request.headers.get("Authorization", "").split(" ", 1)
        if scheme.lower() != "basic":
            raise ValueError()
        user, password = base64.b64decode(value, validate=True).decode("utf8").split(":", 1)
        if not user:
            raise ValueError()
        entries = Path(config.htpasswd_file).read_text(encoding="utf8").splitlines()
        stored = next(line.split(":", 1)[1] for line in entries if ":" in line and line.split(":", 1)[0] == user)
        if stored.startswith(("$2a$", "$2b$", "$2y$")):
            valid = bcrypt.checkpw(password.encode(), stored.encode())
        elif stored.startswith("$apr1$"):
            valid = apr_md5_crypt.verify(password, stored)
        elif stored.startswith("{SHA}"):
            digest = base64.b64encode(hashlib.sha1(password.encode()).digest()).decode()
            valid = hmac.compare_digest(stored[5:], digest)
        else:
            valid = False
        if not valid:
            raise ValueError()
        return user
    except OSError as error:
        raise HTTPException(503, "找货用户凭据文件不可读，请联系管理员检查配置") from error
    except (ValueError, StopIteration, UnicodeError):
        raise HTTPException(401, "请使用库存系统账号登录", headers={"WWW-Authenticate": 'Basic realm="Inventory System"'})


def require_visual_admin(request: Request) -> str:
    user = require_visual_user(request)
    config = request.app.state.visual_config
    if user not in config.admin_users:
        raise HTTPException(403, "当前账号无权执行AI管理员操作")
    return user
