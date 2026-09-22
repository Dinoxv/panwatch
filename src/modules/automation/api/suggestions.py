"""API kho khuyến nghị"""
import logging
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from src.platform.persistence.database import get_db
from src.modules.automation.suggestion_pool import (
    get_suggestions_for_stock,
    get_latest_suggestions,
    cleanup_expired_suggestions,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{symbol}")
def get_stock_suggestions(
    symbol: str,
    market: str = Query("", description="市场代码: CN/HK/US"),
    include_expired: bool = Query(False, description="是否包含已过期建议"),
    limit: int = Query(10, description="返回数量限制"),
    db: Session = Depends(get_db),
):
    """
    Lấy mọi khuyến nghị của một mã

    Trả về danh sách khuyến nghị của mã đó, xếp theo thời gian giảm dần
    """
    suggestions = get_suggestions_for_stock(
        stock_symbol=symbol,
        stock_market=(market or "").strip().upper() or None,
        include_expired=include_expired,
        limit=limit,
    )
    return suggestions


@router.get("/", name="get_suggestions")
@router.get("", include_in_schema=False)  # Xử lý luôn trường hợp không có dấu gạch chéo
def get_all_latest_suggestions(
    symbols: str = Query(None, description="股票代码列表，逗号分隔"),
    stock_keys: str = Query(
        None, description="市场+代码列表，格式 CN:600519,HK:00700,US:AAPL"
    ),
    include_expired: bool = Query(False, description="是否包含已过期建议"),
    db: Session = Depends(get_db),
):
    """
    Lấy khuyến nghị mới nhất của mọi mã

    Mỗi mã chỉ trả về một khuyến nghị còn hiệu lực mới nhất
    Dùng cho trang vị thế hiện nhanh khuyến nghị mới nhất của từng mã
    """
    symbol_list = None
    if symbols:
        symbol_list = [s.strip() for s in symbols.split(",") if s.strip()]

    key_list = None
    if stock_keys:
        parsed: list[tuple[str, str]] = []
        for part in stock_keys.split(","):
            text = (part or "").strip()
            if not text:
                continue
            if ":" not in text:
                parsed.append((text.strip().upper(), "CN"))
                continue
            market, symbol = text.split(":", 1)
            mkt = (market or "CN").strip().upper() or "CN"
            sym = (symbol or "").strip().upper()
            if sym:
                parsed.append((sym, mkt))
        key_list = parsed or None

    suggestions = get_latest_suggestions(
        stock_symbols=symbol_list,
        stock_keys=key_list,
        include_expired=include_expired,
    )
    return suggestions


@router.delete("/cleanup")
def cleanup_suggestions(
    days: int = Query(7, description="清理多少天前的记录"),
    db: Session = Depends(get_db),
):
    """
    Dọn các bản ghi khuyến nghị đã hết hạn

    Mặc định dọn bản ghi cũ hơn 7 ngày
    """
    count = cleanup_expired_suggestions(days=days)
    return {"deleted": count}
