"""Server-only visual search configuration; no dotenv or secret logging."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from app.data_paths import resolve_inventory_data_dir

ENDPOINT = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"
PREPROCESS_VERSION = "exif-rgb-white-jpeg90-1024-v1"


@dataclass(frozen=True)
class VisualConfig:
    model: str = "qwen3-vl-embedding"
    dimension: int = 2560
    version: str = "v1"
    region: str = "cn-beijing"
    endpoint: str = ENDPOINT
    api_key: str = field(default="", repr=False)
    timeout: float = 30
    retries: int = 1
    interval: float = 1
    min_score: float = 0
    htpasswd_file: str = ""
    admin_users: tuple[str, ...] = ()
    allowed_origins: tuple[str, ...] = ("http://localhost:3002", "http://127.0.0.1:3002")
    image_price: float = 0.0018
    text_price: float = 0.0007

    def __post_init__(self):
        if not (1 <= self.dimension <= 4096) or not (1 <= self.timeout <= 60):
            raise ValueError("视觉模型维度或超时配置无效")
        if not (0 <= self.retries <= 2 and 0.1 <= self.interval <= 60):
            raise ValueError("视觉模型重试或速率配置无效")
        if not (-1 <= self.min_score <= 1) or min(self.image_price, self.text_price) < 0:
            raise ValueError("相似度阈值或计费配置无效")
        endpoint = urlparse(self.endpoint)
        if endpoint.scheme != "https" or endpoint.username or endpoint.password or endpoint.query:
            raise ValueError("模型端点必须使用 HTTPS 且不含凭据或查询参数")
        if not endpoint.hostname or not endpoint.hostname.endswith(".aliyuncs.com"):
            raise ValueError("模型端点必须是阿里云端点")
        if self.region != "cn-beijing":
            raise ValueError("首版仅启用北京地域；更换地域需核对官方端点和新建索引版本")
        if self.model == "qwen3-vl-embedding" and self.dimension not in {256, 512, 768, 1024, 1536, 2048, 2560}:
            raise ValueError("qwen3-vl-embedding 不支持该维度")

    @property
    def index_key(self) -> str:
        # Also isolate deployments/workspaces and preprocessing changes.
        import hashlib
        identity = f"{self.model}|{self.dimension}|{self.version}|{self.region}|{self.endpoint}|{PREPROCESS_VERSION}"
        return hashlib.sha256(identity.encode()).hexdigest()

    @classmethod
    def from_env(cls):
        return cls(
            model=os.getenv("VISUAL_EMBEDDING_MODEL", "qwen3-vl-embedding"),
            dimension=int(os.getenv("VISUAL_EMBEDDING_DIMENSION", "2560")),
            version=os.getenv("VISUAL_EMBEDDING_VERSION", "v1"),
            region=os.getenv("VISUAL_EMBEDDING_REGION", "cn-beijing"),
            endpoint=os.getenv("VISUAL_EMBEDDING_ENDPOINT", ENDPOINT),
            api_key=os.getenv("DASHSCOPE_API_KEY", "").strip(),
            timeout=float(os.getenv("VISUAL_EMBEDDING_TIMEOUT", "30")),
            retries=int(os.getenv("VISUAL_EMBEDDING_RETRIES", "1")),
            interval=float(os.getenv("VISUAL_INDEX_INTERVAL", "1")),
            min_score=float(os.getenv("VISUAL_MIN_SCORE", "0")),
            htpasswd_file=os.getenv("INVENTORY_HTPASSWD_FILE", ""),
            admin_users=tuple(x.strip() for x in os.getenv("VISUAL_ADMIN_USERS", "").split(",") if x.strip()),
            allowed_origins=tuple(x.strip().rstrip("/") for x in os.getenv("VISUAL_ALLOWED_ORIGINS", "http://localhost:3002,http://127.0.0.1:3002").split(",") if x.strip()),
            image_price=float(os.getenv("VISUAL_IMAGE_PRICE_PER_1K", "0.0018")),
            text_price=float(os.getenv("VISUAL_TEXT_PRICE_PER_1K", "0.0007")),
        )


def visual_directory(data_root: Path | None = None) -> Path:
    return (data_root or resolve_inventory_data_dir()) / "visual-search"
