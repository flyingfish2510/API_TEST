# YAML 用例语法

本框架以 YAML 为一等公民，测试人员**只写 YAML、不写代码**。一个用例文件是「YAML 列表」，每个元素只能是 `config` / `setup` / `test` / `teardown` 之一。

## 一、基本结构

```yaml
- config:                 # 可选，建议放第一个
    name: "电商-用户登录测试"
    priority: "P0"        # P0 / P1 / P2 / P3
    tags: ["smoke", "p0", "ecommerce"]

- setup:                  # 前置准备（可选，可多个）
    - include: "admin_login.yaml"     # 复用公共片段
    - test:
        name: "[前置] 生成测试数据"
        variables:                      # 直接注入上下文变量（造数）
          test_username: "{{faker.unique_user_name}}"
          test_phone: "{{faker.unique_phone}}"

- test:                   # 主测试步骤（至少 1 个）
    name: "用户登录-正确账号密码"
    request:
      method: POST
      url: "/dar/user/login"
      headers:
        Content-Type: "application/json"
      data:
        user_name: "{{env.accounts.admin.username}}"
        passwd: "{{crypto.md5('admin123')}}"
    extract:              # 从响应中提取变量
      login_token: "$.token"
    validate:             # 断言列表
      - eq: ["status_code", 200]
      - eq: ["$.msg_code", 200]
      - not_eq: ["$.msg_code", 500]
      - not_null: ["$.token"]
      - schema: "login_response.json"

- teardown:               # 后置清理（可选）
    - test:
        name: "[清理] 删除测试用户"
        request: { method: POST, url: "/char/user/deleteUser", data: { user_id: "{{new_user_id}}" } }
        ignore_errors: true
```

## 二、变量语法 `{{...}}`

| 命名空间 | 示例 | 说明 |
|---|---|---|
| `env.` | `{{env.accounts.admin.username}}` | 读取环境配置 |
| `context.` | `{{context.login_token}}` | 读取上下文（含 setup 提取/注入的变量） |
| `faker.` | `{{faker.unique_user_name}}` | 生成测试数据 |
| `time.` | `{{time.offset_days(365)}}` | 时间偏移 |
| `crypto.` | `{{crypto.md5('xxx')}}` | 加密（md5/aes/base64） |
| `random.` | `{{random.int(1, 100)}}` | 随机数 |
| `jsonpath.` | `{{jsonpath.last('$.data.id')}}` | 从上一次响应提取 |

> **整串单占位符**会保留原始类型（`"{{...}}"` 返回 int/dict 而非字符串）。

### 无命名空间
直接写 `{{token}}` 等价于读取上下文变量，例如 setup 中 `variables` 注入的 `test_username`。

## 三、`extract` 提取

```yaml
extract:
  # 简写：JSONPath
  order_no: "$.orderNumber"

  # 简写：纯占位符 → 直接取值（不查响应）
  biz_id: "{{faker.uuid}}"

  # 完整写法
  token:
    source: "json"          # json / regex / header / cookie
    path: "$.token"

  raw:
    source: "regex"
    pattern: '"phone":\s*"(\d+)"'
    group: 1

  sid:
    source: "cookie"
    name: "JSESSIONID"
```

## 四、`variables` 直接注入（造数最佳实践）

不想发请求、只想生成数据供后续步骤复用时，用 `variables`：

```yaml
- test:
    name: "[前置] 生成唯一测试数据"
    variables:
      test_username: "{{faker.unique_user_name}}"
      test_password: "{{faker.password}}"
      test_phone: "{{faker.unique_phone}}"
      test_dates: "{{time.offset_days(365)}}"
```

> 注意：不要用「发一个 `/api/health` 再 extract 变量」的写法——`extract` 是「从响应里抽取」，而造数应使用 `variables`。

## 五、断言类型（`validate`）

| 断言 | 写法 | 说明 |
|---|---|---|
| `status_code` | `["status_code", 200]` / `[200]` | HTTP 状态码 |
| `eq` | `["$.error", 200]` | JSONPath 精确相等 |
| `not_eq` | `["$.msg_code", 200]` | 不相等（验证异常场景） |
| `contains` | `["$.msg", "成功"]` | 包含子串 |
| `not_contains` | `["$.msg", "错误"]` | 不包含子串 |
| `exists` / `not_null` | `["$.token"]` | 字段存在且非空 |
| `schema` | `"login_response.json"` | JSON Schema 契约校验 |
| `max_time` / `p95` | `[800]` | 性能阈值（ms） |
| `db_eq` | `["SELECT ...", "PAID"]` | 数据库断言 |

## 六、`include` 片段复用

公共步骤放在 `data/fragments/` 或用例同目录，通过 `include` 引用：

```yaml
- setup:
    - include: "admin_login.yaml"
```

## 七、目录约定

```
testcases/
├── ecommerce/       # 电商模块
└── logistics/       # 物流模块
```

模块名由 `testcases/<module>/` 路径自动推断，用于 `--module` 过滤与按模块统计。
