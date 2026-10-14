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
    paid_calls = True

    def __init__(self, config: VisualConfig, record_call: Callable, transport=None, *, budget=None, runtime_switch=None):
        self.config = config
        self.record_call = record_call
        self.transport = transport
        self.budget = budget
        self.runtime_switch = runtime_switch

    def embed(self, image: bytes, purpose: str, task_id: str) -> Embedding:
        if self.runtime_switch is None:
            raise VisualError("AI找货当前已关闭，请联系管理员", 503)
        self.runtime_switch.require_enabled()
        if not self.config.api_key:
            raise VisualError("尚未配置百炼 API Key，请联系管理员配置后再搜索", 503)
        if self.budget is None:
            raise VisualError("AI每日调用预算尚未配置，已暂停模型请求", 503)
        reservation = self.budget.reserve()
        payload = {
            "model": self.config.model,
            "input": {"contents": [{"image": "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")}]},
            "parameters": {"dimension": self.config.dimension},
        }
        started = time.perf_counter()
        usage, request_id, failure, result = {}, None, None, None
        try:
            # Recheck immediately before the outbound paid request. A toggle
            # cannot cancel an already-running HTTP call, but stops new calls.
            self.runtime_switch.require_enabled()
            with httpx.Client(timeout=self.config.timeout, transport=self.transport, follow_redirects=False, trust_env=False) as client:
                response = client.post(self.config.endpoint, json=payload, headers={"Authorization": f"Bearer {self.config.api_key}"})
            if response.status_code != 200:
                raise VisualError("百炼服务暂时不可用，请稍后重试", 502)
            body = response.json()
            response_usage = body.get("usage") or {}
            if not isinstance(response_usage, dict):
                raise ValueError("invalid usage")
            usage = response_usage
            request_id = body.get("request_id") if isinstance(body.get("request_id"), str) else None
            embeddings = body["output"]["embeddings"]
            accepted_types = {"image", "fused"}
            # Qwen3's default independent mode returns "vl" for an image.
            if self.config.model == "qwen3-vl-embedding":
                accepted_types.add("vl")
            if len(embeddings) != 1 or embeddings[0].get("type") not in accepted_types:
                raise ValueError("expected one image embedding")
            vector = normalize(embeddings[0]["embedding"], self.config.dimension)
            result = Embedding(vector, usage, request_id)
        except (httpx.TimeoutException, httpx.TransportError) as error:
            failure = VisualError("百炼服务连接超时或不可用，本次不会自动重试", 504)
            failure.__cause__ = error
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            failure = VisualError("百炼响应格式异常，请联系管理员检查模型", 502)
            failure.__cause__ = error
        except VisualError as error:
            failure = error
        finally:
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            record_error = None
            try:
                # Persist only redacted usage and fixed local error descriptions.
                self.record_call(purpose, task_id, usage, request_id, elapsed, failure.detail if failure else None)
            except Exception as error:  # The budget reservation remains durable even if this audit write fails.
                record_error = error
            finally:
                self.budget.settle(reservation, usage, known=True)
            if record_error is not None:
                raise VisualError("视觉调用记录不可用，已暂停后续模型请求", 503) from record_error
        if failure is not None:
            raise failure
        return result
