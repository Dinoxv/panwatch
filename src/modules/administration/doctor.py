"""Tự kiểm tra hệ thống từ dòng lệnh: `python -m src.modules.administration.doctor` hoặc `make doctor`.

Chạy một lượt trên terminal: các mục hạ tầng (DB / đĩa / lập lịch) + nguồn dữ liệu / AI /
thông báo, rồi in kết quả kèm gợi ý khắc phục.
Tiến trình CLI không có bộ lập lịch nào đang chạy → mục lập lịch sẽ được bỏ qua một cách
êm (có ghi rõ lý do chứ không báo lỗi oan).
Mã thoát: có mục bất thường thì trả 1, tất cả đều đạt thì trả 0 (tiện cho CI / script xét).
"""

from __future__ import annotations

import asyncio
import sys

from src.modules.administration.selfcheck import run_selfcheck

_ICON = {"ok": "✅", "slow": "⚠️", "fail": "❌"}
_CAT = {"system": "系统", "datasource": "数据源", "ai": "AI模型", "notify": "通知渠道"}
_ORDER = ["system", "datasource", "ai", "notify"]


def _print_report(res: dict) -> None:
    s = res["summary"]
    print("\n===== PanWatch 系统自检 =====")
    print(f"共 {s['total']} · ✅通 {s['ok']} · ⚠️慢 {s['slow']} · ❌断 {s['fail']}\n")
    items = res.get("items", [])
    for cat in _ORDER:
        cat_items = [i for i in items if i["category"] == cat]
        if not cat_items:
            continue
        print(f"【{_CAT.get(cat, cat)}】")
        for i in cat_items:
            icon = _ICON.get(i["status"], "?")
            grp = f"{i['group']} / " if i.get("group") else ""
            lat = f"  {i['latency_ms']}ms" if i.get("latency_ms") else ""
            print(f"  {icon} {grp}{i['name']}{lat}")
            if i["status"] == "fail":
                if i.get("error"):
                    print(f"       错误: {i['error']}")
                if i.get("hint"):
                    print(f"       建议: {i['hint']}")
            elif i.get("note"):
                print(f"       {i['note']}")
        print()
    if s["fail"]:
        print(f"⚠️  发现 {s['fail']} 项异常,见上方建议。")
    else:
        print("✅ 全部正常。")


def main() -> int:
    res = asyncio.run(run_selfcheck())
    _print_report(res)
    return 1 if res["summary"]["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
