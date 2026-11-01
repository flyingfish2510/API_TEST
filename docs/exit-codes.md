# 退出码（Exit Codes）

本框架的退出码与 `common/exceptions.py` 中的异常体系**强绑定**，供 CI/CD（Jenkins / GitLab）精准判定失败归属。

## 一、退出码总览

| 退出码 | 含义 | 典型场景 | 是否阻断合并 |
|:---:|---|---|:---:|
| **0** | 全部通过 | 所有用例 PASSED | 否 |
| **1** | 业务失败（Failed） | 断言不通过、业务码异常、数据不一致 | 是 |
| **2** | 框架错误（Broken） | 配置错误、YAML 语法错误、依赖缺失、代码 BUG | 是 |
| **3** | 环境不可达（Environment） | 服务未启动、网络不通、DB/Redis 连不上、鉴权失败 | 是（可重试） |
| **4** | 安全拦截（Safety） | 生产环境写操作被阻止、访问敏感接口 | 是 |

## 二、异常分类与退出码映射

```
API_TESTException (基类, code=2)
├── ConfigError                 (2, config)      # 配置/YAML/变量解析错误
│   ├── EnvConfigNotFoundError
│   ├── YamlSyntaxError
│   └── VariableNotResolvedError
├── EnvironmentError            (3, environment) # 环境不可达
│   ├── NetworkError
│   │   └── TimeoutError
│   ├── ServiceUnavailableError
│   ├── DatabaseError
│   └── RedisError
├── AuthError → EnvironmentError (3, auth)       # 鉴权失败
│   ├── TokenExpiredError
│   ├── CredentialMissingError
│   ├── UnauthorizedError (401)
│   └── ForbiddenError (403)
├── BusinessError               (1, business)    # 业务失败（最常见）
│   ├── ApiAssertionError                         # 断言失败
│   ├── SchemaValidationError                     # 契约校验失败
│   ├── SnapshotMismatchError                     # 快照对比失败
│   ├── PerformanceError                          # 性能断言失败
│   └── IdempotencyError                          # 幂等性失败
├── SafetyBlockedError          (4, safety)      # 生产环境拦截
├── StrategyError               (2, strategy)    # 重试耗尽/增量计划错误
├── MockError                   (2, mock)        # Mock 服务错误
└── DataFactoryError            (2, data)        # 造数/清理失败
```

## 三、Failed vs Broken（关键区分）

| 维度 | Failed（1） | Broken（2 / 3 / 4） |
|---|---|---|
| 归属 | 被测系统的业务缺陷 | 框架/环境/安全问题 |
| 修复方 | 后端开发 | 测试框架维护者 / 运维 |
| CI 行为 | 正常阻断，提单 | 告警或重试，可能阻断流水线本身 |

判定依据：`resolve_exit_code(exc)` 会根据异常类型返回对应退出码，`None` 表示成功（0）。

## 四、QualityGate 与退出码

`scripts/quality_gate.py` 在聚合结果后，按 `--mode`（mr/daily/release/inspection）套用不同阈值，并最终产出上述退出码供 Jenkins 使用。
