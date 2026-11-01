# -*- coding: utf-8 -*-
"""
common/utils/file_util.py
-------------------------
文件读写工具类（FileUtil）

支持能力：
    1. YAML 读写（接口自动化用例、环境配置的核心格式）
    2. JSON 读写（Schema、Mock、快照）
    3. 文本文件读写（日志、Markdown、临时文件）
    4. 路径处理（安全拼接、存在性判断）
    5. 批量遍历文件（按后缀过滤）
"""
import os
import json
import logging
from typing import Any, Dict, List, Optional, Union

import yaml

logger = logging.getLogger(__name__)


class FileUtil:
    """文件工具类（全静态方法，无需实例化）"""

    # ============================================================
    # 1. YAML 读写
    # ============================================================
    @staticmethod
    def load_yaml(file_path: str, default: Any = None) -> Any:
        """
        加载 YAML 文件

        :param file_path: 文件路径
        :param default:   文件不存在时的默认返回值（不抛异常的场景）
        :return:          解析后的 Python 对象
        """
        if not os.path.exists(file_path):
            if default is not None:
                return default
            raise FileNotFoundError(f"YAML 文件不存在: {file_path}")

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            logger.debug(f"[File] 加载 YAML: {file_path}")
            return data
        except yaml.YAMLError as e:
            raise ValueError(f"YAML 语法错误 [{file_path}]: {e}") from e

    @staticmethod
    def dump_yaml(data: Any, file_path: str, allow_unicode: bool = True) -> None:
        """
        写入 YAML 文件（保留中文，保持顺序）
        """
        FileUtil.ensure_dir(file_path)
        with open(file_path, "w", encoding="utf-8") as f:
            yaml.dump(
                data,
                f,
                allow_unicode=allow_unicode,
                default_flow_style=False,
                sort_keys=False,
            )
        logger.debug(f"[File] 写入 YAML: {file_path}")

    # ============================================================
    # 2. JSON 读写
    # ============================================================
    @staticmethod
    def load_json(file_path: str, default: Any = None) -> Any:
        """加载 JSON 文件"""
        if not os.path.exists(file_path):
            if default is not None:
                return default
            raise FileNotFoundError(f"JSON 文件不存在: {file_path}")

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.debug(f"[File] 加载 JSON: {file_path}")
            return data
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON 语法错误 [{file_path}]: {e}") from e

    @staticmethod
    def dump_json(data: Any, file_path: str, indent: int = 2,
                  ensure_ascii: bool = False) -> None:
        """写入 JSON 文件（保留中文，格式化缩进）"""
        FileUtil.ensure_dir(file_path)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=ensure_ascii)
        logger.debug(f"[File] 写入 JSON: {file_path}")

    # ============================================================
    # 3. 文本文件读写
    # ============================================================
    @staticmethod
    def read_text(file_path: str, default: Optional[str] = None) -> str:
        """读取文本文件"""
        if not os.path.exists(file_path):
            if default is not None:
                return default
            raise FileNotFoundError(f"文本文件不存在: {file_path}")
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def write_text(content: str, file_path: str) -> None:
        """写入文本文件"""
        FileUtil.ensure_dir(file_path)
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.debug(f"[File] 写入文本: {file_path}")

    @staticmethod
    def append_text(content: str, file_path: str) -> None:
        """追加写入文本（用于日志合并）"""
        FileUtil.ensure_dir(file_path)
        with open(file_path, "a", encoding="utf-8") as f:
            f.write(content)

    # ============================================================
    # 4. 路径处理
    # ============================================================
    @staticmethod
    def ensure_dir(file_path_or_dir: str) -> None:
        """
        确保目录存在（自动创建）
        如果传入的是文件路径，则创建其父目录
        """
        path = file_path_or_dir
        # 如果没有扩展名且不带路径分隔符，认为是目录
        if not os.path.splitext(path)[1]:
            target_dir = path
        else:
            target_dir = os.path.dirname(path)

        if target_dir and not os.path.exists(target_dir):
            os.makedirs(target_dir, exist_ok=True)
            logger.debug(f"[File] 创建目录: {target_dir}")

    @staticmethod
    def join_path(*parts: str) -> str:
        """安全拼接路径"""
        return os.path.join(*parts)

    @staticmethod
    def exists(file_path: str) -> bool:
        """判断文件/目录是否存在"""
        return os.path.exists(file_path)

    @staticmethod
    def is_file(file_path: str) -> bool:
        """是否为文件"""
        return os.path.isfile(file_path)

    @staticmethod
    def is_dir(path: str) -> bool:
        """是否为目录"""
        return os.path.isdir(path)

    # ============================================================
    # 5. 批量遍历文件
    # ============================================================
    @staticmethod
    def list_files(
            dir_path: str,
            suffix: Union[str, List[str], None] = None,
            recursive: bool = True,
    ) -> List[str]:
        """
        遍历目录下的文件

        :param dir_path:  目录路径
        :param suffix:    文件后缀，如 ".yaml" 或 [".yaml", ".yml"]
        :param recursive: 是否递归
        :return:          匹配的文件绝对/相对路径列表
        """
        if not os.path.isdir(dir_path):
            raise NotADirectoryError(f"不是有效目录: {dir_path}")

        # 统一 suffix 为 tuple
        if suffix is None:
            suffixes = None
        elif isinstance(suffix, str):
            suffixes = (suffix,)
        else:
            suffixes = tuple(suffix)

        result = []
        if recursive:
            for root, _, files in os.walk(dir_path):
                for name in files:
                    if suffixes is None or name.endswith(suffixes):
                        result.append(os.path.join(root, name))
        else:
            for name in os.listdir(dir_path):
                full = os.path.join(dir_path, name)
                if os.path.isfile(full):
                    if suffixes is None or name.endswith(suffixes):
                        result.append(full)

        logger.debug(f"[File] 遍历 {dir_path} 找到 {len(result)} 个文件")
        return sorted(result)

    @staticmethod
    def list_yaml_files(dir_path: str, recursive: bool = True) -> List[str]:
        """便捷：列出目录下所有 YAML 文件"""
        return FileUtil.list_files(dir_path, suffix=[".yaml", ".yml"],
                                   recursive=recursive)

    # ============================================================
    # 6. 安全删除/清理
    # ============================================================
    @staticmethod
    def remove(file_path: str, ignore_missing: bool = True) -> bool:
        """
        删除单个文件
        :return: True 删除成功 / False 文件不存在
        """
        if not os.path.exists(file_path):
            if ignore_missing:
                return False
            raise FileNotFoundError(f"待删除文件不存在: {file_path}")
        os.remove(file_path)
        logger.debug(f"[File] 已删除文件: {file_path}")
        return True

    @staticmethod
    def clean_dir(dir_path: str, keep_gitkeep: bool = True) -> None:
        """
        清空目录内容（不删除目录本身）
        常用于每次执行前清理 allure-results
        """
        if not os.path.isdir(dir_path):
            return
        for name in os.listdir(dir_path):
            if keep_gitkeep and name == ".gitkeep":
                continue
            path = os.path.join(dir_path, name)
            try:
                if os.path.isfile(path):
                    os.remove(path)
                elif os.path.isdir(path):
                    import shutil
                    shutil.rmtree(path)
            except Exception as e:
                logger.warning(f"[File] 清理失败 {path}: {e}")
        logger.debug(f"[File] 清空目录: {dir_path}")

    # ============================================================
    # 7. 通用分发（供 VariableResolver 动态调用）
    # ============================================================
    @classmethod
    def call(cls, method: str, *args, **kwargs):
        """
        动态调用入口：
            {{file.read_text('path.txt')}}
            {{file.load_yaml('x.yaml')}}
        """
        if not hasattr(cls, method):
            raise AttributeError(f"FileUtil 无此方法: {method}")
        return getattr(cls, method)(*args, **kwargs)