from __future__ import annotations

import base64
import os


DPAPI_PREFIX = "dpapi:"


class SecretProtectionError(RuntimeError):
    pass


def protect_secret(value: str) -> str:
    secret = str(value or "")
    if not secret:
        return ""
    if os.name != "nt":
        raise SecretProtectionError("Mật khẩu proxy chỉ có thể được mã hóa trên Windows.")
    try:
        import win32crypt

        result = win32crypt.CryptProtectData(
            secret.encode("utf-8"),
            "Dyna proxy credential",
            None,
            None,
            None,
            0,
        )
        encrypted = result[1] if isinstance(result, tuple) else result
    except Exception as exc:
        raise SecretProtectionError("Không thể mã hóa mật khẩu proxy bằng Windows DPAPI.") from exc
    return DPAPI_PREFIX + base64.b64encode(encrypted).decode("ascii")


def unprotect_secret(value: str) -> str:
    protected = str(value or "").strip()
    if not protected:
        return ""
    if not protected.startswith(DPAPI_PREFIX):
        raise SecretProtectionError("Định dạng mật khẩu proxy đã mã hóa không hợp lệ.")
    if os.name != "nt":
        raise SecretProtectionError("Mật khẩu proxy chỉ có thể được giải mã trên Windows.")
    try:
        import win32crypt

        encrypted = base64.b64decode(
            protected.removeprefix(DPAPI_PREFIX),
            validate=True,
        )
        result = win32crypt.CryptUnprotectData(
            encrypted,
            None,
            None,
            None,
            0,
        )
        decrypted = result[1] if isinstance(result, tuple) else result
        return decrypted.decode("utf-8")
    except Exception as exc:
        raise SecretProtectionError(
            "Không thể giải mã mật khẩu proxy trên tài khoản Windows hiện tại."
        ) from exc
