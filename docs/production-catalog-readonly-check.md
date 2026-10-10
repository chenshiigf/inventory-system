# Phase 1.6：由用户执行的生产只读预检

本地代码审查已完成：现有商品、分类、仓库 GET 可复用；包装为一对多，`packing_qty` 是装箱数，`carton_count` 不同步；主图为相对 key。生产镜像需与开发商品 ID、详情访问和向量目录隔离。

按照用户最新指令，代理不登录服务器，不使用 `inventory-deploy` 密钥，不下载生产图库。下列命令由用户审核后在既有 SSH 会话执行，仅返回少量检查结果。执行预检不代表授权后续全量传输。

## 1. 在 Windows PowerShell 登录

```powershell
ssh root@8.136.127.226
```

密码由本人在 SSH 终端输入，不发送给代理。

## 2. 在服务器终端执行

**不要把 Markdown 的三个反引号或 `bash` 标记粘贴到终端。** 请使用代码块的“复制”按钮，只复制命令内容。若已误贴这些标记，先按 Ctrl+C，确认重新出现 `root@…:~#` 提示符；不要继续补贴长脚本，也不要按 Ctrl+D 退出 SSH。

如果长脚本粘贴困难，先单独运行这一条，反馈输出后再分步检查 API：

```bash
systemctl show inventory-system-api.service --property=ActiveState,SubState,MainPID,User,WorkingDirectory,EnvironmentFiles
```

整个代码块一起复制。使用系统 Python 标准库，`-B` 禁止生成 pycache；脚本通过 stdin 运行，不上传脚本文件。只读取 systemd 状态、少量代码及运行进程中的两个路径环境变量，调用回环地址的现有 GET/HEAD。不会直接打开 SQLite、读 htpasswd、构建、迁移、启停服务或写入业务数据；不下载图片内容。

```bash
systemctl show inventory-system-api.service --property=ActiveState,SubState,MainPID,User,WorkingDirectory,EnvironmentFiles
python3 -B - <<'PY'
import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

def emit(label, value):
    print(label + ': ' + json.dumps(value, ensure_ascii=False))

# Only expose the two data-path settings; never print full environment.
try:
    pid = int(subprocess.check_output([
        'systemctl', 'show', 'inventory-system-api.service',
        '--property=MainPID', '--value'
    ], text=True).strip())
    if pid <= 0:
        raise ValueError('service has no active PID')
    paths = {}
    for entry in Path('/proc/' + str(pid) + '/environ').read_bytes().split(b'\0'):
        key, sep, value = entry.partition(b'=')
        if sep and key in (b'INVENTORY_DATA_DIR', b'DATABASE_URL'):
            setting = value.decode('utf8', errors='replace')
            if key == b'DATABASE_URL' and (
                not setting.startswith('sqlite:///') or
                any(char in setting for char in ('?', '#', '@'))
            ):
                setting = '[non-plain-SQLite setting omitted]'
            paths[key.decode()] = setting
    emit('running_data_paths', paths)
except (OSError, ValueError, subprocess.SubprocessError) as error:
    emit('running_data_paths_error', type(error).__name__)

# Read code bytes only; do not import/execute the production app.
root = Path('/srv/inventory-system')
files = [
    'api/app/models.py', 'api/app/schemas.py',
    'api/app/routers/products.py', 'api/app/services/image_storage.py',
    'api/app/data_paths.py'
]
hashes = {}
for relative in files:
    try:
        hashes[relative] = hashlib.sha256(
            (root / relative).read_bytes().replace(b'\r\n', b'\n')
        ).hexdigest()
    except OSError:
        hashes[relative] = '[unreadable or absent]'
emit('production_code_sha256', hashes)

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

opener = build_opener(ProxyHandler({}), NoRedirect())
base = 'http://127.0.0.1:8102'

def get_json(path):
    with opener.open(Request(base + path, method='GET'), timeout=10) as response:
        data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise ValueError('response exceeds preflight limit')
        return json.loads(data)

try:
    first = None
    for status in ('all', 'active', 'inactive'):
        result = get_json('/api/products?page=1&page_size=1&status=' + status)
        emit('product_count_' + status, result.get('total'))
        if status == 'all':
            items = result.get('items') or []
            first = items[0] if items else None
            emit('list_response_fields', sorted(result))
            emit('product_fields', sorted(first) if first else [])
            packagings = (first or {}).get('packagings') or []
            emit('packaging_fields', sorted(packagings[0]) if packagings else [])

    categories = get_json('/api/categories')
    def count_categories(nodes):
        return sum(1 + count_categories(n.get('children') or []) for n in nodes)
    emit('category_count', count_categories(categories))
    emit('warehouse_count', len(get_json('/api/warehouses')))

    # At most one image HEAD. No image response body is downloaded.
    identifier = (first or {}).get('image_path')
    if identifier:
        relative = PurePosixPath(identifier)
        if (relative.is_absolute() or '..' in relative.parts or
            any(char in identifier for char in ('\\', ':', '\0', '?', '#'))):
            emit('sample_image', 'unsafe reference; no request sent')
        else:
            with opener.open(Request(base + '/uploads/' + quote(identifier, safe='/'),
                                     method='HEAD'), timeout=10) as response:
                emit('sample_image_headers', {
                    'product_id': first.get('id'), 'status': response.status,
                    'content_type': response.headers.get('Content-Type'),
                    'content_length': response.headers.get('Content-Length')
                })
    else:
        emit('sample_image', 'first product has no image; no image request sent')
except HTTPError as error:
    emit('api_error', {'type': 'HTTPError', 'status': error.code})
except (URLError, TimeoutError, OSError, ValueError, TypeError, KeyError,
        AttributeError, RecursionError) as error:
    emit('api_error', {'type': type(error).__name__})
PY
```

如果 systemd 服务名、代码目录或 8102 端口不同，命令只会报告失败，不会修复或创建任何东西；将失败结果反馈，下一步再调整检查命令。不输出商品价格、箱数、备注、分类/仓库名称、账号密码、API Key 或完整商品 JSON。

三个商品计数分开查询，不能当作同一时刻的事务快照。首款没有图时不会扫描其他商品或批量找图；生产整体有图比例、图片总字节数、实际 SQLite DDL 和完整性尚未由此命令验证。这组命令用于确认运行路径、当前 API 契约、总量及代码差异，足够决定后续 API 同步是否适用。正常访问日志可能记录这些 GET/HEAD。

## 3. 反馈结果

将输出文本反馈即可。无须发送密码、密钥、环境文件全文、htpasswd 或生产数据库。代理比对结果后更新方案，再单独等待生产图库传输确认。
