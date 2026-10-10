"""DashScope native multimodal embedding. Tests inject a provider; no runtime mock."""
import base64
import time
from dataclasses import dataclass
from typing import Callable

import httpx
import numpy as np

from .config import VisualConfig
from .images import VisualError


def normalize(vector, dimension: int) -> np.ndarray:
    try:
        result = np.asarray(vector, dtype=np.float32)
    except (TypeError, ValueError, OverflowError) as error:
        raise VisualError("模型返回的向量格式无效", 502) from error
    if result.shape != (dimension,) or not np.all(np.isfinite(result)):
        raise VisualError("向量维度或数值不一致，需要检查模型配置并重新索引", 502)
    length = float(np.linalg.norm(result))
    if not np.isfinite(length) or length <= 0:
        raise VisualError("模型返回了无效的零向量", 502)
    return result / length


@dataclass
class Embedding:
    vector: np.ndarray
    usage: dict
    request_id: str | None = None


class DashScopeProvider:
    def __init__(self, config: VisualConfig, record_call: Callable, transport=None):
        self.config = config
        self.record_call = record_call
        self.transport = transport

    def embed(self, image: bytes, purpose: str, task_id: str) -> Embedding:
        if not self.config.api_key:
            raise VisualError("尚未配置百炼 API Key，请联系管理员配置后再搜索", 503)
        payload = {
            "model": self.config.model,
            "input": {"contents": [{"image": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")}]},
            "parameters": {"dimension": self.config.dimension},
        }
        for attempt in range(self.config.retries + 1):
            started = time.perf_counter()
            usage, request_id, failure = {}, None, None
            retry = False
            try:
                with httpx.Client(timeout=self.config.timeout, transport=self.transport, follow_redirects=False, trust_env=False) as client:
                    response = client.post(self.config.endpoint, json=payload, headers={"Authorization": f"Bearer {self.config.api_key}"})
                if response.status_code != 200:
                    retry = response.status_code in {429, 500, 502, 503, 504}
                    raise VisualError("百炼服务暂时繁忙，请稍后重试" if retry else "百炼调用被拒绝，请管理员检查地域、模型和 API Key", 502)
                body = response.json()
                usage = body.get("usage") or {}
                if not isinstance(usage, dict):
                    raise ValueError("invalid usage")
                request_id = body.get("request_id")
                embeddings = body["output"]["embeddings"]
                if len(embeddings) != 1 or embeddings[0].get("type") not in {"image", "fused"}:
                    raise ValueError("expected one image embedding")
                vector = normalize(embeddings[0]["embedding"], self.config.dimension)
                return Embedding(vector, usage, request_id)
            except (httpx.TimeoutException, httpx.TransportError) as error:
                retry = True
                failure = VisualError("百炼服务连接超时或不可用，请稍后重试", 504)
                failure.__cause__ = error
            except (KeyError, TypeError, ValueError) as error:
                failure = VisualError("百炼响应格式异常，请联系管理员检查模型", 502)
                failure.__cause__ = error
            except VisualError as error:
                failure = error
            finally:
                # Do not persist response messages, request payloads, keys or Base64.
                self.record_call(purpose, task_id, usage, request_id, round((time.perf_counter() - started) * 1000, 2), failure.detail if failure else None)
            if not retry or attempt == self.config.retries:
                raise failure
            time.sleep(min(2 ** attempt, 4))
