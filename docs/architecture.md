# 架构概览

## 一、设计目标

企业级、数据驱动的 HTTP 接口自动化测试框架：**测试人员只写 YAML，Python 代码作为引擎驱动数据**。面向多环境、多业务、可进 CI 的接口测试需求。

## 二、整体分层

```
┌─────────────────────────────────────────────────────────────┐
│  CI 层   ci/Jenkinsfile.{mr,daily,release,inspection}        │
│          scripts/*（quality_gate / dry_run / notify / …）     │
├─────────────────────────────────────────────────────────────┤
│  入口层   run.py → pytest.main()                              │
│          testcases/conftest.py（Fixture、参数、插件注册）      │
│          core/plugins/pytest_plugins.py（YAML 收集器）        │
├─────────────────────────────────────────────────────────────┤
│  引擎层   core/engine/                                        │
│          YamlLoader → VariableResolver → EnvGuard            │
│          → RequestBuilder → Extractor → AssertFactory        │
│          （由 YamlRunner 编排 setup/steps/teardown）           │
├─────────────────────────────────────────────────────────────┤
│  能力层   common/（HttpClient / AuthManager / Context /      │
│          Faker / DB / Utils / Exceptions）                   │
│          config/（多环境 YAML + 环境变量覆盖）                 │
├─────────────────────────────────────────────────────────────┤
│  支撑层   core/assertions（7 类断言）  core/strategy（重试/增量）│
│          core/cleanup（TrashBin）  core/reporter（质量报告）   │
│          core/mock（本地 Mock Server） core/models（Pydantic）  │
└─────────────────────────────────────────────────────────────┘
```

## 三、用例执行链路

```
pytest 收集 .yaml → YamlItem.setup() 获取 Session 级 fixture（api_client / auth_manager）
        ↓
YamlItem.runtest() → YamlLoader.load() 解析为扁平结构
        ↓
YamlRunner.run_case()
   ├── Phase1 Setup    （含 variables 造数 / include 片段）
   ├── Phase2 Test     （VariableResolver → EnvGuard → RequestBuilder → Extractor → AssertFactory）
   └── Phase3 Teardown （失败也兜底执行清理）
        ↓
YamlItem 持久化登录态（token / cookies → 全局 context）
```

## 四、质量保障要素

- **异常分级 + 退出码**：Failed(1) / Broken(2) / Environment(3) / Safety(4)，详见 `exit-codes.md`
- **生产安全守卫**：`EnvGuard` 代码级拦截生产写操作、高危 SQL
- **数据治理闭环**：`TrashBin` 逆序清理 + `variables` 造数 + 快照基线
- **断言可插拔**：`AssertFactory.register()` 扩展新断言
- **可观测**：Trace-ID 全链路、Allure 步骤级附件、QualityReporter 多维统计
- **CI 深度适配**：Git diff 增量执行、四档质量门禁

## 五、相关文档

- `yaml-syntax.md` — YAML 用例语法
- `exit-codes.md` — 退出码与异常体系
- `mock-guide.md` — Mock 服务使用
- `env-management.md` — 多环境配置
- `ci-integration.md` — CI 集成
