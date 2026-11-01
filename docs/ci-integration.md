# CI 集成指南

本框架提供四条 Jenkins 流水线，覆盖从 MR 门禁到发版、每日回归、生产巡检的完整场景。

## 一、流水线一览

| 文件 | 用途 | 触发 | 特征 |
|---|---|---|---|
| `ci/Jenkinsfile.mr` | Merge Request 冒烟门禁 | MR 创建/更新 | 增量执行、失败阻断合并、<5 分钟 |
| `ci/Jenkinsfile.daily` | 每日全量回归 | cron `H 6 * * *` | 按模块并行、Allure+JUnit、邮件通知 |
| `ci/Jenkinsfile.release` | 发版前置门禁 | 手动触发 | 强制全量 P0+P1、双重门禁、版本留档 |
| `ci/Jenkinsfile.inspection` | 生产只读巡检 | cron `H/30 * * * *` | 强制只读、失败告警、轻量报告 |

## 二、所需 Jenkins 插件

- **Allure Jenkins Plugin** — 生成 Allure 报告
- **Copy Artifact Plugin** — 跨构建恢复 Allure history（**趋势图必需**）
- **JUnit Plugin** — 原生测试结果
- **Email Extension Plugin** — 邮件通知
- **GitLab Plugin**（可选）— MR 状态回写
- **Docker Pipeline** — 容器内执行

## 三、Allure 历史趋势（history）跨构建累积

### 为什么需要
Allure 的趋势/历史图依赖 `allure-results/history`。在**干净的 agent / Docker 容器**中，工作区不持久，若不显式恢复，每次构建的趋势图都只有 1 个点。

### 流水线中的实现
每条流水线（mr / daily / release）做了两件事：

1. **构建开始时恢复**（Stage 1，mkdir 之后）：
   ```groovy
   restoreAllureHistory()   // 从上一成功构建归档中拷贝 history 到 allure-results/history
   ```
2. **构建结束时归档**（post always）：
   ```groovy
   archiveArtifacts artifacts: "reports/allure-report/history/**, ..."
   ```

`restoreAllureHistory()` 的定义（各 Jenkinsfile 末尾）：
```groovy
def restoreAllureHistory() {
    try {
        copyArtifacts(
            projectName: env.JOB_NAME,
            selector: lastSuccessful(),
            filter: 'reports/allure-report/history/**',
            target: '<REPORT_DIR>/history-tmp',
            flatten: true,
            optional: true
        )
        sh "mkdir -p <REPORT_DIR>/history && cp -r <REPORT_DIR>/history-tmp/* <REPORT_DIR>/history/ || true"
    } catch (Exception e) {
        echo "[WARN] 恢复 Allure history 失败（首次构建可忽略）: ${e.message}"
    }
}
```

> `<REPORT_DIR>` 因流水线而异：mr=`reports/allure-results-mr`，daily=`reports/allure-results`，release=`reports/allure-results-release`。

### 关键点
- `selector: lastSuccessful()`：只继承**成功构建**的历史，避免失败构建污染趋势。
- `optional: true`：首次构建无历史时不报错。
- `flatten: true` + `history-tmp`：扁平拷贝，再合并到 `history/`，规避目录层级问题。
- **inspection 不启用**：它按设计不生成完整 Allure 报告，且每小时运行，避免存储膨胀。

## 四、本地复现 history 机制

本地运行（`scripts/run_with_allure.ps1`）同样维护历史：
```powershell
.\scripts\run_with_allure.ps1 -Open
```
脚本会在清空 results 前缓存上次 `allure-report/history`，再回灌到 `allure-results/history`，效果与 Jenkins 一致。

手动等价命令：
```powershell
# 1. 恢复上次 history
Copy-Item reports/allure-report/history reports/allure-results/history -Recurse -Force
# 2. 跑测试
python -m pytest
# 3. 生成报告
allure generate reports/allure-results -o reports/allure-report --clean
```

## 五、质量门禁与退出码

`scripts/quality_gate.py` 支持 `--mode=mr|daily|release|inspection`，按模式套用阈值（见 `ci/quality-gate-rules.json`）。退出码见 `docs/exit-codes.md`。

## 六、注意事项

- **Copy Artifact Plugin 权限**：若 Jenkins 有权限管控，需确保 Job 有 `CopyArtifact` 权限，否则 `copyArtifacts` 会抛异常（已被 try/catch 吞掉，只影响趋势图）。
- **保留策略**：`buildDiscarder` 需保留足够多的历史构建（建议 `artifactNumToKeepStr >= 10`），否则 `lastSuccessful` 找不到带 history 的产物。
- **并发**：daily/release 已 `disableConcurrentBuilds()`，保证 history 不被并发篡改。
