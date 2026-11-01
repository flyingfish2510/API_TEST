# ============================================================
# docker/Dockerfile
# API_TEST 测试执行器镜像
#
# 构建命令：
#   cd API_TEST
#   docker build -f docker/Dockerfile -t api-test-runner:latest .
#
# 运行命令：
#   docker run --rm -v $(pwd)/reports:/app/reports api-test-runner:latest
# ============================================================

# ---------- 基础镜像 ----------
FROM python:3.11-slim AS base

# ---------- 元数据 ----------
LABEL maintainer="QA Team <qa@yourcompany.com>" \
      framework="API_TEST" \
      version="1.0.0" \
      description="API_TEST 企业级接口自动化测试执行器"

# ---------- 环境变量 ----------
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    APP_HOME=/app

# ---------- 系统依赖 ----------
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        # Allure CLI 依赖
        default-jre-headless \
        # 时区配置
        tzdata \
        # 健康检查工具
        curl \
        # git（部分用例可能需要查 commit）
        git \
        # 编译工具（部分 Python 包需要）
        build-essential \
        # SSL 证书（HTTPS 请求必需）
        ca-certificates; \
    # 时区
    ln -sf /usr/share/zoneinfo/${TZ} /etc/localtime; \
    echo ${TZ} > /etc/timezone; \
    # 清理
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# ---------- 安装 Allure CLI ----------
ARG ALLURE_VERSION=2.24.0
RUN set -eux; \
    curl -fsSL -o /tmp/allure.tgz \
        "https://github.com/allure-framework/allure2/releases/download/${ALLURE_VERSION}/allure-${ALLURE_VERSION}.tgz"; \
    mkdir -p /opt/allure; \
    tar -xzf /tmp/allure.tgz -C /opt/allure --strip-components=1; \
    ln -sf /opt/allure/bin/allure /usr/local/bin/allure; \
    rm /tmp/allure.tgz; \
    allure --version

# ---------- 工作目录 ----------
WORKDIR ${APP_HOME}

# ---------- 1. 先复制依赖清单（利用缓存）----------
COPY requirements.txt ./

# ---------- 2. 安装 Python 依赖（依赖层，缓存命中率高）----------
RUN pip install --no-cache-dir -r requirements.txt

# ---------- 3. 创建运行时目录（在 .dockerignore 中被排除，但这里需要预建）----------
RUN mkdir -p \
    reports/allure-results \
    reports/allure-report \
    reports/junit \
    reports/logs \
    reports/artifacts \
    data/snapshots

# ---------- 4. 复制项目代码（代码层，每次变更都重新执行）----------
# 注意：.dockerignore 会自动排除 .git / .env / reports/* 等
COPY . .

# ---------- 5. 创建非 root 用户（安全）----------
RUN set -eux; \
    groupadd -r testuser && useradd -r -g testuser -d ${APP_HOME} testuser; \
    chown -R testuser:testuser ${APP_HOME}

USER testuser

# ---------- 健康检查 ----------
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import sys; sys.exit(0)"

# ---------- 默认命令 ----------
# 可通过 docker run 覆盖，例如：
#   docker run api-test-runner pytest testcases/ecommerce/ -v
CMD ["pytest", "testcases/", "--env=test", "--alluredir=reports/allure-results", "-v"]