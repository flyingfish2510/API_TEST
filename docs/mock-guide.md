# Mock 服务指南

框架内置一个**零第三方依赖**的本地 Mock HTTP 服务（基于标准库 `http.server`），用于模拟第三方依赖（如支付网关、短信服务）或后端未就绪的接口。

## 一、组件

| 组件 | 文件 | 职责 |
|---|---|---|
| `MockServer` | `core/mock/mock_server.py` | HTTP 服务管理（启停、端口检测、规则匹配、延迟模拟） |
| `MockLoader` | `core/mock/mock_loader.py` | 加载 `data/mocks/*.json` 规则文件 |
| `MockRule` | `core/mock/mock_loader.py` | 单条规则模型（method/path/query/body 匹配 + 响应渲染） |

## 二、规则文件格式（`data/mocks/*.json`）

```json
[
  {
    "name": "支付成功",
    "method": "POST",
    "path": "/mock/pay",
    "query": {},
    "body_match": {},
    "response": {
      "status": 200,
      "headers": { "Content-Type": "application/json" },
      "body": { "code": 0, "msg": "success" },
      "delay": 0.5
    }
  }
]
```

字段说明：

| 字段 | 说明 |
|---|---|
| `method` | HTTP 方法（GET/POST/…），可省略表示任意 |
| `path` | 请求路径（精确或正则） |
| `query` / `body_match` | 可选的查询参数 / 请求体匹配条件 |
| `response.status` | 返回状态码 |
| `response.body` | 返回体（dict/list/str） |
| `response.delay` | 模拟网络延迟（秒），用于超时场景 |

## 三、编程式使用

```python
from core.mock import MockServer, MockLoader

rules = MockLoader.load("payment_gateway.json")
server = MockServer(host="127.0.0.1", port=8788)
server.add_rules(rules)
server.start()          # 自动等待端口就绪
try:
    # ... 执行测试，将业务配置指向 http://127.0.0.1:8788 ...
    pass
finally:
    server.stop()
```

或使用上下文管理器：

```python
with MockServer(port=8788) as server:
    server.add_rules(MockLoader.load("payment_gateway.json"))
    ...
```

## 四、与真实后端的关系

- **`api.base_url`**（如 `http://127.0.0.1:8787`）指向被测后端。
- **Mock 服务**（默认 `8788`）仅用于模拟**第三方依赖**。
- 若下游第三方未就绪，可在 `config/env/<env>.yaml` 中调整相关 URL 指向 Mock 端口。

## 五、端口冲突

`MockServer.start()` 会先做端口可用性检测，被占用时抛出 `RuntimeError` 并提示检查占用进程。测试环境建议固定端口并在 CI 中显式暴露。

## 六、可用规则

当前 `data/mocks/` 内置：`payment_gateway.json`（支付网关）、`sms_service.json`（短信服务）。
