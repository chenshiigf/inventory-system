"""Check the real local API with hidden credentials, without paid model requests."""
import argparse
import base64
import getpass
import json
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


def main():
    parser = argparse.ArgumentParser(description="本地视觉鉴权检查；不调用付费模型，不保存密码")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--via-web", action="store_true", help="通过前端 3002 同源代理检查，而非直连 8002")
    args = parser.parse_args()
    if not args.user or any(c in args.user for c in ":\r\n"):
        parser.error("用户名无效")
    password = getpass.getpass("本地管理员密码（隐藏输入，不保存）：")
    if not password:
        parser.error("密码不能为空")
    authorization = "Basic " + base64.b64encode((args.user + ":" + password).encode()).decode()
    password = None
    opener = build_opener(ProxyHandler({}), NoRedirect())
    base = "http://127.0.0.1:" + ("3002" if args.via_web else "8002") + "/api/visual-search"

    def request(path, *, auth=True, body=None, extra=None):
        headers = {"Authorization": authorization} if auth else {}
        headers.update(extra or {})
        req = Request(base + path + "?source=production-catalog", data=body, headers=headers)
        try:
            response = opener.open(req, timeout=90)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    code, _ = request("/index/status", auth=False)
    if code != 401:
        raise ValueError("未登录请求未按预期返回 401")
    code, status = request("/index/status")
    if code != 200:
        raise ValueError("管理员登录检查失败：HTTP " + str(code))
    ready = (status.get("schema_ready") is True and status.get("api_key_configured") is True
             and status.get("source_id") == "aliyun-inventory-production"
             and status.get("indexed") == 2414 and status.get("indexed_unique_images") == 2380
             and status.get("model") == "qwen3-vl-embedding" and status.get("dimension") == 2560
             and status.get("unindexed") == 0 and status.get("failed") == 0)
    if not ready:
        raise ValueError("生产图库、模型或 API Key 状态尚未就绪")

    boundary = "LocalVisualAuthProbe"
    # Intentionally invalid image bytes fail before vectors, query persistence or the provider.
    body = ("--" + boundary + '\r\nContent-Disposition: form-data; name="file"; filename="auth-probe.jpg"'
            + '\r\nContent-Type: image/jpeg\r\n\r\nnot-a-valid-image\r\n--' + boundary
            + '\r\nContent-Disposition: form-data; name="top_k"\r\n\r\n5\r\n--' + boundary + '--\r\n').encode()
    headers = {"Content-Type": "multipart/form-data; boundary=" + boundary,
               "Origin": "http://127.0.0.1:3002", "Sec-Fetch-Site": "same-origin" if args.via_web else "same-site"}
    code, error = request("/search", body=body, extra=headers)
    if code != 400 or error.get("detail") != "无法读取有效图片，请重新选择 JPG、PNG 或 WEBP":
        raise ValueError("搜索入口未按预期进入图片校验：HTTP " + str(code))
    code, _ = request("/search", body=body, extra={**headers, "Sec-Fetch-Site": "cross-site"})
    if code != 403:
        raise ValueError("跨站请求未按预期拒绝")
    code, after = request("/index/status")
    if code != 200 or after.get("usage", {}).get("api_calls") != status.get("usage", {}).get("api_calls"):
        raise ValueError("检查期间 API 调用账本出现变化，请人工核对")
    print(json.dumps({"ready": ready, "source": status["source"], "source_id": status["source_id"],
                      "transport": "Next.js same-origin proxy" if args.via_web else "direct local FastAPI",
                      "schema_ready": status["schema_ready"], "api_key_configured": status["api_key_configured"],
                      "indexed": status["indexed"], "unique_vectors": status["indexed_unique_images"],
                      "unauthenticated_status": 401, "search_image_validation": 400,
                      "cross_site_status": 403, "paid_calls_added": 0}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as error:
        raise SystemExit("本地检查失败：" + str(error)) from None
