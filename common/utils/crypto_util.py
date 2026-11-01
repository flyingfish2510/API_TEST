# -*- coding: utf-8 -*-
"""
common/utils/crypto_util.py
---------------------------
加解密工具类（CryptoUtil）

支持能力：
    1. MD5（含加盐）              —— 文档要求：承运商密码 MD5 加密
    2. SHA256                     —— 通用摘要
    3. HMAC-SHA256                —— 网关/回调签名
    4. Base64 编解码               —— 报文编码
    5. AES-128/192/256 (CBC/ECB)  —— 对称加解密
    6. RSA 签名与验签              —— 非对称加密（对账/回调）
    7. UUID / 随机串               —— 测试数据辅助

依赖：
    pip install pycryptodome
"""
import base64
import hashlib
import hmac
import os
import uuid
import logging
from typing import Optional, Union

try:
    from Crypto.Cipher import AES, PKCS1_v1_5 as Cipher_pkcs1_v1_5
    from Crypto.PublicKey import RSA
    from Crypto.Signature import pkcs1_15
    from Crypto.Hash import SHA256
    from Crypto.Util.Padding import pad, unpad

    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False
    AES = RSA = None

logger = logging.getLogger(__name__)


class CryptoUtil:
    """加解密工具类（全静态方法，无需实例化）"""

    # ============================================================
    # 1. MD5（文档要求：承运商密码 MD5 加密）
    # ============================================================
    @staticmethod
    def md5(text: str, salt: Optional[str] = None, upper: bool = False) -> str:
        """
        MD5 哈希（可选加盐）

        :param text: 待哈希原文
        :param salt:  可选盐值（前置拼接）
        :param upper: 是否返回大写（部分网关要求大写）
        :return: 32 位十六进制字符串

        示例：
            >>> CryptoUtil.md5("202cb962ac59075b964b07152d234b70")  # 文档示例密码
            '202cb962ac59075b964b07152d234b70'  # 注意：文档示例本身已是 MD5
            >>> CryptoUtil.md5("Test@123")
            'f2e6d3...'
        """
        if text is None:
            text = ""
        s = (salt + text) if salt else text
        result = hashlib.md5(s.encode("utf-8")).hexdigest()
        return result.upper() if upper else result

    @staticmethod
    def md5_bytes(data: bytes, salt: Optional[str] = None) -> str:
        """对二进制数据进行 MD5"""
        s = (salt.encode() + data) if salt else data
        return hashlib.md5(s).hexdigest()

    @staticmethod
    def md5_file(file_path: str, chunk_size: int = 8192) -> str:
        """
        计算文件的 MD5（用于校验上传文件完整性）
        """
        m = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(chunk_size), b""):
                m.update(chunk)
        return m.hexdigest()

    # ============================================================
    # 2. SHA256
    # ============================================================
    @staticmethod
    def sha256(text: str, upper: bool = False) -> str:
        """SHA256 摘要"""
        result = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return result.upper() if upper else result

    @staticmethod
    def sha1(text: str, upper: bool = False) -> str:
        """SHA1 摘要（老系统兼容）"""
        result = hashlib.sha1(text.encode("utf-8")).hexdigest()
        return result.upper() if upper else result

    # ============================================================
    # 3. HMAC-SHA256（网关/回调签名）
    # ============================================================
    @staticmethod
    def hmac_sha256(text: str, key: str, upper: bool = False) -> str:
        """
        HMAC-SHA256 签名

        :param text: 待签名原文
        :param key:  签名密钥
        :param upper: 是否大写
        """
        result = hmac.new(
            key.encode("utf-8"),
            text.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return result.upper() if upper else result

    # ============================================================
    # 4. Base64 编解码
    # ============================================================
    @staticmethod
    def base64_encode(text: Union[str, bytes]) -> str:
        """Base64 编码"""
        if isinstance(text, str):
            text = text.encode("utf-8")
        return base64.b64encode(text).decode("utf-8")

    @staticmethod
    def base64_decode(text: str) -> bytes:
        """Base64 解码"""
        return base64.b64decode(text)

    @staticmethod
    def base64_urlsafe_encode(text: Union[str, bytes]) -> str:
        """URL 安全的 Base64 编码（JWT / URL 参数场景）"""
        if isinstance(text, str):
            text = text.encode("utf-8")
        return base64.urlsafe_b64encode(text).decode("utf-8").rstrip("=")

    @staticmethod
    def base64_urlsafe_decode(text: str) -> bytes:
        """URL 安全的 Base64 解码"""
        padding = "=" * (4 - len(text) % 4)
        return base64.urlsafe_b64decode(text + padding)

    # ============================================================
    # 5. AES 加解密（CBC / ECB）
    # ============================================================
    @staticmethod
    def aes_encrypt(
            plaintext: str,
            key: str,
            iv: Optional[str] = None,
            mode: str = "CBC",
            key_size: int = 32,
    ) -> str:
        """
        AES 加密（默认 AES-256-CBC）

        :param plaintext: 明文
        :param key:       密钥（不足会自动补 0 或截断到 key_size 位）
        :param iv:        偏移量（CBC 必须，ECB 忽略）
        :param mode:      'CBC' 或 'ECB'
        :param key_size:  16/24/32（对应 AES-128/192/256）
        :return:          Base64 编码的密文

        示例：
            >>> CryptoUtil.aes_encrypt("hello", "1234567890abcdef")
        """
        CryptoUtil._require_crypto()
        key_bytes = CryptoUtil._normalize_key(key, key_size)
        data = plaintext.encode("utf-8")

        if mode.upper() == "CBC":
            if not iv:
                raise ValueError("AES-CBC 模式必须提供 iv")
            iv_bytes = CryptoUtil._normalize_key(iv, 16)
            cipher = AES.new(key_bytes, AES.MODE_CBC, iv_bytes)
        elif mode.upper() == "ECB":
            cipher = AES.new(key_bytes, AES.MODE_ECB)
        else:
            raise ValueError(f"不支持的 AES 模式: {mode}")

        encrypted = cipher.encrypt(pad(data, AES.block_size))
        return base64.b64encode(encrypted).decode("utf-8")

    @staticmethod
    def aes_decrypt(
            ciphertext_b64: str,
            key: str,
            iv: Optional[str] = None,
            mode: str = "CBC",
            key_size: int = 32,
    ) -> str:
        """AES 解密（密文须为 Base64 编码）"""
        CryptoUtil._require_crypto()
        key_bytes = CryptoUtil._normalize_key(key, key_size)
        data = base64.b64decode(ciphertext_b64)

        if mode.upper() == "CBC":
            if not iv:
                raise ValueError("AES-CBC 模式必须提供 iv")
            iv_bytes = CryptoUtil._normalize_key(iv, 16)
            cipher = AES.new(key_bytes, AES.MODE_CBC, iv_bytes)
        elif mode.upper() == "ECB":
            cipher = AES.new(key_bytes, AES.MODE_ECB)
        else:
            raise ValueError(f"不支持的 AES 模式: {mode}")

        decrypted = unpad(cipher.decrypt(data), AES.block_size)
        return decrypted.decode("utf-8")

    @staticmethod
    def _normalize_key(key: str, size: int) -> bytes:
        """规整密钥长度：不足补 0，超出截断"""
        key_bytes = key.encode("utf-8")
        if len(key_bytes) < size:
            key_bytes = key_bytes.ljust(size, b"\0")
        elif len(key_bytes) > size:
            key_bytes = key_bytes[:size]
        return key_bytes

    # ============================================================
    # 6. RSA 签名与验签（对账/回调场景）
    # ============================================================
    @staticmethod
    def rsa_sign(data: str, private_key_pem: str) -> str:
        """
        RSA-SHA256 签名

        :param data:            待签名原文
        :param private_key_pem: PEM 格式的私钥字符串
        :return:                Base64 编码的签名
        """
        CryptoUtil._require_crypto()
        key = RSA.import_key(private_key_pem)
        h = SHA256.new(data.encode("utf-8"))
        signature = pkcs1_15.new(key).sign(h)
        return base64.b64encode(signature).decode("utf-8")

    @staticmethod
    def rsa_verify(data: str, signature_b64: str, public_key_pem: str) -> bool:
        """
        RSA-SHA256 验签

        :return: True 验签通过 / False 验签失败
        """
        CryptoUtil._require_crypto()
        key = RSA.import_key(public_key_pem)
        h = SHA256.new(data.encode("utf-8"))
        try:
            pkcs1_15.new(key).verify(h, base64.b64decode(signature_b64))
            return True
        except (ValueError, TypeError) as e:
            logger.warning(f"[Crypto] RSA 验签失败: {e}")
            return False

    @staticmethod
    def rsa_encrypt(data: str, public_key_pem: str) -> str:
        """RSA 加密（公钥加密，Base64 输出）"""
        CryptoUtil._require_crypto()
        key = RSA.import_key(public_key_pem)
        cipher = Cipher_pkcs1_v1_5.new(key)
        encrypted = cipher.encrypt(data.encode("utf-8"))
        return base64.b64encode(encrypted).decode("utf-8")

    # ============================================================
    # 7. 辅助方法
    # ============================================================
    @staticmethod
    def uuid_hex() -> str:
        """无连字符 UUID（32 位十六进制）"""
        return uuid.uuid4().hex

    @staticmethod
    def random_token(length: int = 32) -> str:
        """随机字符串（大小写字母 + 数字）"""
        import secrets
        import string
        alphabet = string.ascii_letters + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(length))

    @staticmethod
    def _require_crypto():
        """检查 pycryptodome 是否安装"""
        if not _CRYPTO_AVAILABLE:
            raise ImportError(
                "AES/RSA 功能需要 pycryptodome，请执行: pip install pycryptodome"
            )

    # ============================================================
    # 通用入口（供 YAML 引擎动态调用）
    # ============================================================
    @classmethod
    def call(cls, method: str, *args, **kwargs):
        """
        动态调用入口，供 VariableResolver 解析 {{crypto.md5('xxx')}}
        示例：
            {{crypto.md5('Test@123')}}
            {{crypto.base64_encode('hello')}}
            {{crypto.random_token(16)}}
        """
        if not hasattr(cls, method):
            raise AttributeError(f"CryptoUtil 无此方法: {method}")
        return getattr(cls, method)(*args, **kwargs)