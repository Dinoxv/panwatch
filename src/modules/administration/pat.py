"""Công cụ cho mã truy cập cá nhân (PAT).

Hệ xác thực riêng dành cho endpoint MCP, tách khỏi JWT đăng nhập (tham khảo mô hình
của BeeCount-Cloud):

- Bản rõ mang tiền tố ``pwmcp_`` (để người dùng và bộ quét secret nhận ra, tương tự
  ``ghp_`` của GitHub);
- Chỉ trả bản rõ đúng một lần lúc tạo, trong cơ sở dữ liệu chỉ lưu sha256 (``token_hash``);
- Kiểm tra bằng ``hmac.compare_digest`` để so sánh trong thời gian hằng định, chống
  tấn công đo thời gian;
- Không dùng bcrypt/PBKDF2 — bản thân token đã có 256 bit entropy ngẫu nhiên nên không
  cần chống dò như mật khẩu, mà MCP lại phải kiểm tra ở mỗi lời gọi công cụ, nên sha256
  cộng so sánh thời gian hằng định vừa nhanh vừa đủ an toàn.

Bảo đảm tách luồng (PAT chỉ vào được endpoint MCP):
- API thường đi qua JWT (auth.get_current_user), PAT (tiền tố pwmcp_) không phải JWT
  hợp lệ → bị từ chối;
- Endpoint MCP kiểm tra PAT, JWT không mang tiền tố pwmcp_ → bị từ chối.
"""

import hashlib
import hmac
import secrets

PAT_PREFIX = "pwmcp_"
PAT_RANDOM_BYTES = 32          # sau token_urlsafe khoảng 43 ký tự, entropy 256 bit
PAT_DISPLAY_PREFIX_LEN = 14    # 14 ký tự đầu của bản rõ, ví dụ pwmcp_a1b2c3d4

# Phạm vi MCP (toàn bộ chỉ đọc)
SCOPE_MCP_READ = "mcp:read"


def hash_token(token: str) -> str:
    """Tóm tắt sha256 dạng thập lục phân."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_pat() -> tuple[str, str, str]:
    """Sinh một PAT.

    Returns:
        (plaintext, token_hash, display_prefix) — plaintext chỉ được trả về đúng một lần lúc tạo.
    """
    raw = secrets.token_urlsafe(PAT_RANDOM_BYTES)
    plaintext = f"{PAT_PREFIX}{raw}"
    return plaintext, hash_token(plaintext), plaintext[:PAT_DISPLAY_PREFIX_LEN]


def looks_like_pat(token: str) -> bool:
    """Nhận biết PAT qua tiền tố (để tách luồng xác thực, khỏi phải giải mã hai lần ở mỗi request)."""
    return bool(token) and token.startswith(PAT_PREFIX)


def verify_pat_hash(provided_token: str, stored_hash: str) -> bool:
    """So sánh sha256 của PAT trong thời gian hằng định, chống tấn công đo thời gian."""
    return hmac.compare_digest(hash_token(provided_token), stored_hash)
