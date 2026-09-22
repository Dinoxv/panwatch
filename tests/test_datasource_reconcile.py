"""数据源表温和对账(server.reconcile_data_sources):补齐缺失默认 + 删孤儿,保留用户自定义不动。"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import src.platform.persistence.models as M  # noqa: F401  bảo đảm mô hình được đăng ký vào Base.metadata
from src.platform.persistence.database import Base
from src.platform.persistence.models import DataSource

import server


def _make_session():
    """独立内存 sqlite,不碰真实 data/panwatch.db。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_reconcile_deletes_orphans_keeps_user_custom_and_fills_missing_defaults():
    """对账:删孤儿(news/cls、kline/tushare),保留用户自定义 quote/tencent(config/priority 原样),补回缺失默认。"""
    db = _make_session()

    # Bản ghi mồ côi 1: news/cls — vừa không thuộc tập engine trong gói marketdata, vừa không nằm trong danh sách khởi tạo hiện tại
    db.add(
        DataSource(
            name="财联社电报",
            type="news",
            provider="cls",
            config={"rn": 50},
            enabled=True,
            priority=2,
            supports_batch=False,
            test_symbols=[],
        )
    )
    # Bản ghi mồ côi 2: kline/tushare — Bước 3 đã xóa khỏi danh sách khởi tạo, trong gói cũng không có vendor tương ứng
    db.add(
        DataSource(
            name="Tushare K线",
            type="kline",
            provider="tushare",
            config={"token": "", "description": "旧配置"},
            enabled=False,
            priority=10,
            supports_batch=False,
            test_symbols=["600519"],
        )
    )
    # Tùy chỉnh hợp lệ: quote/tencent là mặc định khởi tạo hợp lệ nhưng người dùng đã sửa config/priority — phải giữ nguyên
    db.add(
        DataSource(
            name="腾讯行情",
            type="quote",
            provider="tencent",
            config={"foo": 1},
            enabled=True,
            priority=99,
            supports_batch=True,
            test_symbols=["600519"],
        )
    )
    db.commit()
    # Cố tình không chèn một mặc định khởi tạo nào đó (ví dụ nến EastMoney) để kiểm chứng reconcile sẽ bù lại

    result = server.reconcile_data_sources(db)

    remaining = {(s.type, s.provider): s for s in db.query(DataSource).all()}

    # Bản ghi mồ côi bị xóa
    assert ("news", "cls") not in remaining
    assert ("kline", "tushare") not in remaining

    # Cấu hình do người dùng tùy chỉnh được giữ nguyên
    kept = remaining[("quote", "tencent")]
    assert kept.config == {"foo": 1}
    assert kept.priority == 99

    # Các mặc định còn thiếu được bù lại
    assert ("kline", "eastmoney") in remaining

    # Trong summary thấy được bản ghi đã xóa
    deleted_pairs = {(d["type"], d["provider"]) for d in result["deleted"]}
    assert ("news", "cls") in deleted_pairs
    assert ("kline", "tushare") in deleted_pairs
    assert ("quote", "tencent") not in deleted_pairs

    db.close()


def test_reconcile_refreshes_legacy_seed_test_symbols_but_keeps_custom_values():
    """升级默认测试股票时,旧种子/旧 APPL 拼写应迁移,真正自定义值不能被覆盖。"""
    db = _make_session()
    db.add(
        DataSource(
            name="腾讯K线",
            type="kline",
            provider="tencent",
            config={},
            enabled=True,
            priority=0,
            supports_batch=False,
            test_symbols=["601127", "600519", "300750", "APPL"],
        )
    )
    db.add(
        DataSource(
            name="自定义 K线",
            type="kline",
            provider="tencent",
            config={"custom": True},
            enabled=True,
            priority=99,
            supports_batch=False,
            test_symbols=["688981"],
        )
    )
    db.commit()

    server.reconcile_data_sources(db, reset_test_symbols=True)
    rows = db.query(DataSource).filter(DataSource.type == "kline").all()
    seeded = next(row for row in rows if row.name == "腾讯K线")
    custom = next(row for row in rows if row.name == "自定义 K线")

    assert seeded.test_symbols == ["600519", "601127", "00700", "00386", "AAPL", "NVDA"]
    assert custom.test_symbols == ["688981"]
    db.close()
