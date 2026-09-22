"""Thí điểm Planning — dàn dựng do kế hoạch dẫn dắt cho "soi toàn diện danh mục của tôi".

Phạm vi cố ý để nhỏ: chỉ phủ một tình huống duy nhất (soi toàn diện danh mục). Nhận ra ý
định đó thì đi theo lối do kế hoạch dẫn dắt: LLM sinh kế hoạch có cấu trúc (phân tích
từng mã trong danh mục → rủi ro danh mục → khuyến nghị tổng hợp) → kế hoạch được đẩy cho
frontend qua sự kiện SSE `plan` → chạy từng bước (mỗi bước dùng lại công cụ/LLM sẵn có) →
bước nào hỏng thì lập lại kế hoạch (tối đa 1 lần, quá thì mang luôn thông tin hỏng vào
phần tổng hợp).

Đây là **thí điểm**: để kiểm chứng giá trị của "do kế hoạch dẫn dắt" so với luồng cố
định, không tổng quát hóa quá tay. Hàm dàn dựng nhận bộ chạy công cụ (execute_tool) và
luồng SSE (stream) dưới dạng phụ thuộc tiêm vào, để unit test mock được hết.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

# Từ khóa kích hoạt: khớp tường minh là chuyển sang chế độ dẫn dắt theo kế hoạch (heuristic đơn giản, đủ cho giai đoạn thử nghiệm)
_PLANNING_TRIGGERS = (
    "全面诊断",
    "诊断我的持仓",
    "诊断一下我的持仓",
    "持仓诊断",
    "组合诊断",
    "全面体检",
    "持仓体检",
    "全面分析我的持仓",
)


def should_use_planning(content: str) -> bool:
    """Xét xem người dùng nhập vào có rơi vào tình huống "soi toàn diện danh mục" không."""
    if not content:
        return False
    text = content.replace(" ", "")
    return any(t in text for t in _PLANNING_TRIGGERS)


_PLAN_SYSTEM = (
    "你是投资组合诊断规划助手。根据用户持仓,产出一个结构化诊断计划。"
    "只输出 JSON,形如:"
    '{"steps":[{"title":"分析 贵州茅台(600519)","action":"analyze_stock",'
    '"params":{"symbol":"600519","market":"CN"}},'
    '{"title":"组合整体风险","action":"portfolio_risk"}]}。'
    "action 取值:analyze_stock(逐只持仓,params 带 symbol/market)、portfolio_risk(组合风险)。"
    "不要包含汇总步骤,汇总由系统自动追加。"
)


def _plan_messages(portfolio_text: str) -> list[dict]:
    return [
        {"role": "system", "content": _PLAN_SYSTEM},
        {"role": "user", "content": f"我的持仓如下,请产出诊断计划:\n{portfolio_text}"},
    ]


def _replan_messages(
    portfolio_text: str, failed_title: str, error: str
) -> list[dict]:
    return [
        {"role": "system", "content": _PLAN_SYSTEM},
        {
            "role": "user",
            "content": (
                f"我的持仓:\n{portfolio_text}\n\n"
                f'上一版计划里的步骤「{failed_title}」执行失败({error}),'
                "请重新产出一份可执行的诊断计划(跳过或替换失败步骤)。"
            ),
        },
    ]


def parse_plan(text: str) -> list[dict] | None:
    """Đọc danh sách bước kế hoạch từ văn bản LLM theo kiểu chịu lỗi.

    Hỗ trợ: JSON thuần, bọc trong hàng rào ```json, có chữ giải thích ở trước/sau, bị cắt
    cụt ở đuôi và các kiểu đầu ra bẩn thường gặp khác.
    Đọc hỏng thì trả None (để bên gọi lùi về kế hoạch mặc định).
    """
    if not text:
        return None

    blob = None
    m = re.search(r"```(?:json)?\s*([\[{].*?[\]}])\s*```", text, re.S)
    if m:
        blob = m.group(1)
    else:
        candidates = [i for i in (text.find("{"), text.find("[")) if i >= 0]
        if candidates:
            blob = text[min(candidates):]

    if not blob:
        return None

    data = None
    for attempt in (blob, blob[: max(blob.rfind("]"), blob.rfind("}")) + 1]):
        try:
            data = json.loads(attempt)
            break
        except Exception:
            continue
    if data is None:
        return None

    if isinstance(data, dict):
        data = data.get("steps") or data.get("plan")
    if not isinstance(data, list) or not data:
        return None
    return data


def build_default_plan(portfolio_text: str) -> list[dict]:
    """Kế hoạch mặc định khi hạ cấp lúc kế hoạch của LLM không dùng được (chỉ làm phần rủi ro danh mục, phần tổng hợp do hệ thống thêm vào)."""
    return [{"title": "组合整体风险评估", "action": "portfolio_risk"}]


def normalize_steps(steps: list[dict], start_id: int = 1) -> list[dict]:
    """Chuẩn hóa bước: bù id/title/action/params/status. Lọc bỏ summarize (phần tổng hợp hệ thống tự làm)."""
    out = []
    sid = start_id
    for s in steps:
        if not isinstance(s, dict):
            continue
        action = s.get("action") or "portfolio_risk"
        if action == "summarize":
            continue
        out.append(
            {
                "id": sid,
                "title": s.get("title") or f"步骤 {sid}",
                "action": action,
                "params": s.get("params") or {},
                "status": "pending",
            }
        )
        sid += 1
    return out


def _steps_public(steps: list[dict]) -> list[dict]:
    return [{"id": s["id"], "title": s["title"], "status": s["status"]} for s in steps]


async def _publish_plan(stream, steps: list[dict], status: str, current=None) -> None:
    data = {"status": status, "steps": _steps_public(steps)}
    if current is not None:
        data["current"] = current
    await stream.publish("plan", data)


_STEP_SYSTEM = "你是资深投研分析师。基于给定数据,给出精炼、有据的分析(150 字内)。"
_SUMMARY_SYSTEM = (
    "你是资深投资顾问。基于各步骤的分析结果,给出全面的持仓诊断结论:"
    "整体健康度、主要风险、可执行的调仓建议。分点、精炼、有据。"
)


async def _execute_step(db, ai_client, execute_tool, step: dict, portfolio_text: str) -> str:
    """Chạy một bước kế hoạch, trả về văn bản phân tích của bước đó."""
    action = step["action"]
    if action == "analyze_stock":
        p = step.get("params") or {}
        symbol = p.get("symbol", "")
        market = p.get("market", "CN")
        tech = await execute_tool(db, "get_technical_analysis", {"symbol": symbol, "market": market})
        sug = await execute_tool(db, "get_stock_suggestions", {"symbol": symbol, "market": market})
        msgs = [
            {"role": "system", "content": _STEP_SYSTEM},
            {
                "role": "user",
                "content": f"分析持仓「{step['title']}」。\n技术面:\n{tech}\n\nAI 建议:\n{sug}",
            },
        ]
        return await ai_client.chat_multi(msgs, temperature=0.4)

    # portfolio_risk và các action lạ khác: xử lý thống nhất như rủi ro danh mục
    msgs = [
        {"role": "system", "content": _STEP_SYSTEM},
        {"role": "user", "content": f"评估以下持仓组合的整体风险:\n{portfolio_text}"},
    ]
    return await ai_client.chat_multi(msgs, temperature=0.4)


def _summary_messages(results: list[tuple[str, str]]) -> list[dict]:
    body = "\n\n".join(f"【{title}】\n{res}" for title, res in results)
    return [
        {"role": "system", "content": _SUMMARY_SYSTEM},
        {"role": "user", "content": f"以下是各步骤的诊断结果,请汇总:\n\n{body}"},
    ]


async def run_portfolio_diagnosis(db, stream, ai_client, execute_tool) -> str:
    """Dàn dựng "soi toàn diện danh mục" do kế hoạch dẫn dắt, trả về văn bản tổng hợp cuối (đã đẩy theo luồng qua SSE).

    Args:
        db: phiên DB.
        stream: SSEStream (cần hỗ trợ async publish(event, data)).
        ai_client: máy khách AI (chat_multi / chat_stream).
        execute_tool: bộ chạy công cụ async (db, name, args) -> str.
    """
    await stream.publish("plan", {"status": "planning", "steps": []})

    portfolio_text = await execute_tool(db, "get_portfolio", {})

    # 1) Sinh kế hoạch (thất bại / không bóc được thì lùi về kế hoạch mặc định)
    steps = None
    try:
        raw = await ai_client.chat_multi(_plan_messages(portfolio_text), temperature=0.3)
        steps = parse_plan(raw)
    except Exception:
        logger.warning("生成诊断计划失败,回退默认计划", exc_info=True)
    if not steps:
        steps = build_default_plan(portfolio_text)
    steps = normalize_steps(steps)
    if not steps:
        steps = normalize_steps(build_default_plan(portfolio_text))

    await _publish_plan(stream, steps, status="running")

    # 2) Thực thi từng bước, hỏng thì lập lại kế hoạch (tối đa 1 lần)
    results: list[tuple[str, str]] = []
    replanned = False
    i = 0
    while i < len(steps):
        step = steps[i]
        step["status"] = "running"
        await _publish_plan(stream, steps, status="running", current=step["id"])
        try:
            res = await _execute_step(db, ai_client, execute_tool, step, portfolio_text)
            step["status"] = "done"
            results.append((step["title"], res))
        except Exception as e:  # noqa: BLE001
            if not replanned:
                replanned = True
                logger.info("步骤「%s」失败,触发重规划: %s", step["title"], e)
                try:
                    raw = await ai_client.chat_multi(
                        _replan_messages(portfolio_text, step["title"], str(e)),
                        temperature=0.3,
                    )
                    new_steps = parse_plan(raw)
                except Exception:
                    new_steps = None
                if new_steps:
                    steps = steps[:i] + normalize_steps(new_steps, start_id=step["id"])
                    await _publish_plan(stream, steps, status="running")
                    continue  # Thử lại từ vị trí hiện tại bằng kế hoạch mới
            # Đã lập lại kế hoạch hoặc việc lập lại thất bại: đánh dấu thất bại, mang thông tin lỗi đi tổng hợp tiếp
            step["status"] = "failed"
            results.append((step["title"], f"(该步执行失败:{e})"))
        await _publish_plan(stream, steps, status="running")
        i += 1

    # 3) Tổng hợp (đẩy token theo luồng)
    summary = ""
    try:
        parts: list[str] = []
        async for kind, payload in ai_client.chat_stream(
            _summary_messages(results), temperature=0.4
        ):
            if kind == "token":
                parts.append(payload)
                await stream.publish("token", {"text": payload})
        summary = "".join(parts)
    except Exception as e:  # noqa: BLE001 — tổng hợp theo luồng thất bại thì hạ cấp sang không dùng luồng
        logger.warning("流式汇总失败,降级非流式: %s", e)
        try:
            summary = await ai_client.chat_multi(_summary_messages(results), temperature=0.4)
            await stream.publish("token", {"text": summary})
        except Exception:
            summary = "抱歉,诊断汇总失败。"
            await stream.publish("token", {"text": summary})

    await _publish_plan(stream, steps, status="done")
    return summary
