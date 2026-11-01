# -*- coding: utf-8 -*-
"""
scripts/__init__.py
-------------------
API_TEST 框架辅助脚本模块

本目录下的脚本用于 CI/CD 流水线或运维手动调用，通常以独立命令运行：
    python scripts/health_check.py --env=test
    python scripts/quality_gate.py --report=xxx.json
    python scripts/notify.py --status=SUCCESS

注意：这些脚本**不直接作为模块被框架 import**，因此本文件保持极简。
"""

__version__ = "1.0.0"