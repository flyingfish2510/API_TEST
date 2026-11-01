# ============================================================
# docker/mock-server.Dockerfile
# Mock Server 独立镜像
#
# 构建命令：
#   cd API_TEST
#   docker build -f docker/mock-server.Dockerfile -t api-test-mock:latest .
#
# 运行命令（单服务）：
#   docker run --rm -p 8788:8788 -p 8789:8789 api-test-mock:latest
#
# 运行命令（多规则）：
#   docker run --rm -p 8788:8788 -e MOCK_RULES="payment_gateway.json,sms_service.json" api-test-mock:latest
# ============================================================

# ---------- 基础镜像 ----------
FROM python:3.11-slim AS base

# ---------- 元数据 ----------
LABEL maintainer="QA Team <qa@yourcompany.com>" \
      framework="API_TEST" \
      component="mock-server" \
      version="1.0.0" \
      description="API_TEST 独立 Mock 服务（模拟第三方依赖）"

# ---------- 环境变量 ----------
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    APP_HOME=/app \
    MOCK_RULES_DIR=/app/data/mocks \
    MOCK_HOST=0.0.0.0 \
    MOCK_PORT=8788 \
    MOCK_RULES=""

# ---------- 系统依赖（极简，无需 JRE/git）----------
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        tzdata \
        ca-certificates; \
    ln -sf /usr/share/zoneinfo/${TZ} /etc/localtime; \
    echo ${TZ} > /etc/timezone; \
    apt-get clean; \
    rm -rf /var/lib/apt/lists/*

# ---------- 工作目录 ----------
WORKDIR ${APP_HOME}

# ---------- 1. 创建最小依赖清单（只装 Mock 需要的）----------
COPY requirements.txt /tmp/requirements-full.txt

RUN set -eux; \
    # 只保留 Mock 服务需要的核心依赖
    grep -E "^(pydantic|PyYAML|loguru|requests)" /tmp/requirements-full.txt > requirements-mock.txt || true; \
    pip install --no-cache-dir -r requirements-mock.txt; \
    rm /tmp/requirements-full.txt requirements-mock.txt

# ---------- 2. 只复制 Mock 服务所需代码 ----------
# 核心依赖（core.mock + core.models + common）
COPY core/__init__.py ./core/__init__.py
COPY core/mock/ ./core/mock/
COPY core/models/ ./core/models/
COPY core/cleanup/ ./core/cleanup/__init__.py
COPY core/cleanup/trash_bin.py ./core/cleanup/trash_bin.py

# 公共基础设施
COPY common/ ./common/

# 配置
COPY config/__init__.py ./config/__init__.py
COPY config/settings.py ./config/settings.py
COPY config/env/ ./config/env/

# Mock 规则文件
COPY data/mocks/ ./data/mocks/

# Mock 入口脚本
COPY scripts/start_mock.py ./scripts/start_mock.py

# ---------- 3. 创建非 root 用户 ----------
RUN set -eux; \
    groupadd -r mockuser && useradd -r -g mockuser -d ${APP_HOME} mockuser; \
    chown -R mockuser:mockuser ${APP_HOME}

USER mockuser

# ---------- 4. 暴露 Mock 端口 ----------
# 8788 = 支付网关
# 8789 = 短信服务
# 8790 = SRM 物流
EXPOSE 8788 8789 8790

# ---------- 5. 健康检查 ----------
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import sys, urllib.request; \
        urllib.request.urlopen('http://127.0.0.1:${MOCK_PORT}/health', timeout=3); \
        sys.exit(0)" \
    || exit 1

# ---------- 6. 启动命令 ----------
CMD ["python", "-m", "scripts.start_mock"]