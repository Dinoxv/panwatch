"""Route SPA không được phục vụ tệp nằm ngoài thư mục static.

Trước khi vá, handler ghép thẳng `os.path.join(static_dir, path)` rồi trả
FileResponse. Đã dựng lại handler đó trên uvicorn và khai thác được thật:

    GET /../secret.txt        → 200, trả nguyên nội dung tệp ngoài static_dir
    GET /%2e%2e/secret.txt    → 200, y hệt (uvicorn giải percent-encoding
                                trước khi định tuyến, nên qua được cả proxy
                                có chuẩn hóa URL)

Trong layout Docker (WORKDIR /app, static ở /app/static, DB ở /app/data) chỉ
cần MỘT cấp traversal là lấy được /app/data/panwatch.db — toàn bộ vị thế,
khóa API của LLM, token bot thông báo, hash mật khẩu và jwt_secret. Route này
nằm ngoài mọi Depends(get_current_user) nên không cần đăng nhập.
"""

import os

import pytest

from src.web.static_files import resolve_static_file


@pytest.fixture()
def static_root(tmp_path):
    """Dựng cây thư mục mô phỏng layout Docker: /app/static cạnh /app/data."""
    app = tmp_path / "app"
    static = app / "static"
    static.mkdir(parents=True)
    (static / "index.html").write_text("<html>index</html>", encoding="utf-8")
    (static / "assets").mkdir()
    (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    data = app / "data"
    data.mkdir()
    (data / "panwatch.db").write_text("SQLITE_SECRET", encoding="utf-8")
    (app / ".env").write_text("AI_API_KEY=secret", encoding="utf-8")
    return static


def test_serves_a_real_file_inside_static(static_root):
    """Đường dẫn hợp lệ vẫn phục vụ bình thường."""
    got = resolve_static_file(static_root, "assets/app.js")
    assert got is not None and got.read_text(encoding="utf-8") == "console.log(1)"


def test_serves_index(static_root):
    got = resolve_static_file(static_root, "index.html")
    assert got is not None and got.name == "index.html"


@pytest.mark.parametrize(
    "attack",
    [
        "../data/panwatch.db",       # đúng vector khai thác được trên uvicorn
        "../.env",
        "../../etc/hostname",
        "assets/../../data/panwatch.db",   # traversal lồng giữa đường dẫn
        "./../data/panwatch.db",
        "..%2fdata",                        # chuỗi đã giải mã một phần
        "/etc/passwd",                      # tuyệt đối: pathlib vứt vế trái
        "//etc/passwd",
        "\\..\\data\\panwatch.db",
    ],
)
def test_traversal_is_refused(static_root, attack):
    """Mọi biến thể thoát thư mục đều trả None → người gọi rơi về index.html."""
    assert resolve_static_file(static_root, attack) is None


def test_symlink_pointing_outside_is_refused(static_root):
    """Symlink nằm trong static nhưng trỏ ra ngoài cũng bị chặn.

    resolve() khai triển symlink nên phép kiểm tra chứa thư mục bắt được,
    khác với os.path.normpath vốn chỉ xử lý '..' trên chuỗi.
    """
    outside = static_root.parent / "data" / "panwatch.db"
    link = static_root / "leak.db"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("hệ thống tệp không tạo được symlink")
    assert resolve_static_file(static_root, "leak.db") is None


def test_directory_is_not_served(static_root):
    """Thư mục không phải tệp — không phục vụ, tránh lộ danh sách nội dung."""
    assert resolve_static_file(static_root, "assets") is None


def test_empty_path_falls_through(static_root):
    """Đường dẫn rỗng (trang gốc) rơi về index.html ở tầng gọi."""
    assert resolve_static_file(static_root, "") is None
    assert resolve_static_file(static_root, "/") is None


def test_missing_file_falls_through(static_root):
    """Tệp không tồn tại rơi về index.html — hành vi SPA bình thường."""
    assert resolve_static_file(static_root, "portfolio") is None


def test_refusal_does_not_leak_existence(static_root):
    """Tệp ngoài static dù tồn tại hay không đều trả cùng một kết quả.

    Nếu phân biệt hai trường hợp, attacker dò được sự tồn tại của tệp trên máy.
    """
    assert resolve_static_file(static_root, "../data/panwatch.db") is None
    assert resolve_static_file(static_root, "../data/khong-ton-tai.db") is None
