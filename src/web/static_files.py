"""Giải đường dẫn tệp tĩnh cho SPA, có kiểm tra chứa thư mục.

Route bắt-tất của SPA nhận thẳng phần đường dẫn từ URL rồi ghép vào thư mục
static. Nếu chỉ ghép mà không kiểm tra kết quả có còn nằm trong thư mục đó
không, client gửi ``GET /../data/panwatch.db`` sẽ tải về nguyên cơ sở dữ liệu:
vị thế, khóa API của LLM, token bot thông báo, hash mật khẩu và ``jwt_secret``
— có ``jwt_secret`` là giả mạo được token quản trị. Route này nằm ngoài mọi
``Depends(get_current_user)`` nên không cần đăng nhập.

uvicorn KHÔNG chuẩn hóa ``..`` trong đường dẫn, và nó giải mã percent-encoding
trước khi định tuyến, nên cả ``/../x`` lẫn ``/%2e%2e/x`` đều tới được handler.

Điểm chốt an toàn duy nhất là phép kiểm tra chứa thư mục sau khi ``resolve()``:
resolve khai triển cả ``..`` lẫn symlink, nên một symlink nằm trong static mà
trỏ ra ngoài cũng bị chặn.
"""

from __future__ import annotations

from pathlib import Path


def resolve_static_file(static_dir: str | Path, url_path: str) -> Path | None:
    """Trả về tệp nằm trong ``static_dir`` tương ứng ``url_path``, hoặc None.

    None nghĩa là "không phục vụ đường dẫn này" — người gọi nên trả về
    ``index.html`` như mọi route SPA không khớp tệp, không phải báo lỗi, để
    không lộ ra tệp nào tồn tại ngoài thư mục static.
    """
    root = Path(static_dir).resolve()

    # Bỏ dấu phân cách đứng đầu: Path("/app/static") / "/etc/passwd" cho ra
    # Path("/etc/passwd") — pathlib vứt luôn vế trái khi vế phải là tuyệt đối.
    # Phép kiểm tra chứa thư mục bên dưới vẫn bắt được trường hợp này, đây chỉ
    # là lớp phòng thủ thứ hai.
    relative = url_path.lstrip("/\\")
    if not relative:
        return None

    try:
        target = (root / relative).resolve()
    except (OSError, RuntimeError, ValueError):
        # Đường dẫn quá dài, vòng lặp symlink, byte NUL...
        return None

    if target != root and root not in target.parents:
        return None
    if not target.is_file():
        return None
    return target
