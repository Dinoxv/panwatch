"""Agent 过程评测框架：用例结构、运行器与规则断言引擎。

用例 = 固定输入（问题 + mock 工具数据）→ 规则断言：
- 工具选择正确（该调的调了、不该调的没调、闲聊不调）；
- 工具参数正确；
- 动作在白名单内（只允许 CHAT_TOOLS 注册的只读工具）；
- 答案引用了工具结果（有据性：mock 数据里的关键值必须出现在答案中）；
- 工具失败时优雅降级（不编造无据数值）。

规则断言优先；语义维度（相关性/清晰度）由 judge.py 的 LLM-as-judge 补充。
每个线上 bad case 修复后应固化为一条新用例（加进 cases/chat_cases.py）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from src.modules.assistant.chat_api import SYSTEM_PROMPT
from src.modules.assistant.legacy_chat_tools import CHAT_TOOLS

# Danh sách trắng hành động: chat agent chỉ được gọi các công cụ chỉ đọc này
TOOL_WHITELIST = {t["function"]["name"] for t in CHAT_TOOLS}
MAX_TOOL_ROUNDS = 5

# Giá trị trả về mặc định khi test không cấp dữ liệu giả cho một công cụ (mô phỏng công cụ thất bại)
DEFAULT_TOOL_MISSING = "工具执行出错: eval 用例未提供该工具的 mock 数据"


@dataclass
class ChatEvalCase:
    """一条 chat 工具循环评测用例。"""

    id: str
    question: str
    # Tên công cụ → văn bản trả về giả (tình huống công cụ lỗi thì đưa thẳng câu "工具执行出错: ...")
    tool_data: dict[str, str] = field(default_factory=dict)
    # Các công cụ bắt buộc phải gọi (kiểm tra theo tập con, không đòi thứ tự)
    expected_tools: tuple[str, ...] = ()
    # Các công cụ dứt khoát không được gọi
    forbidden_tools: tuple[str, ...] = ()
    # Câu hỏi tán gẫu / khái niệm: tuyệt đối không được gọi công cụ nào
    expect_no_tools: bool = False
    # Tên công cụ → {tên tham số: giá trị kỳ vọng hoặc hàm kiểm tra}; gọi nhiều lần cùng tên thì chỉ cần một lần khớp là đạt
    param_checks: dict[str, dict] = field(default_factory=dict)
    # Tính có căn cứ: các giá trị then chốt mà câu trả lời phải chứa (khớp hết mới đạt)
    answer_must_contain: tuple[str, ...] = ()
    # Câu trả lời phải chứa ít nhất một trong số này (ví dụ các cách diễn đạt kiểu "thất bại / không thể / chưa thực hiện được" ở tình huống lỗi)
    answer_must_contain_any: tuple[str, ...] = ()
    # Câu trả lời không được chứa (ví dụ khi công cụ lỗi thì không được xuất hiện con số cụ thể bịa ra)
    answer_must_not_contain: tuple[str, ...] = ()
    notes: str = ""


@dataclass
class ChatEvalResult:
    """一次用例运行的过程记录。"""

    case_id: str
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    answer: str = ""
    error: str = ""


class ChatEvalRunner:
    """驱动 chat 工具循环跑一条评测用例（工具执行被 mock 数据替代）。

    ai_client 需实现 `chat_with_tools(messages, tools, temperature) -> message`
    （与 src.platform.ai.ai_client.AIClient 一致）：
    - make eval 时注入真实 AIClient（配置从环境变量读取，见 run_eval.py）；
    - 单测里注入脚本化的假客户端，不发任何真实请求。
    """

    def __init__(self, ai_client, temperature: float = 0.0):
        self.ai_client = ai_client
        # Chấm điểm dùng nhiệt độ thấp để giảm tối đa tính bất định
        self.temperature = temperature

    async def run_case(self, case: ChatEvalCase) -> ChatEvalResult:
        result = ChatEvalResult(case_id=case.id)
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": case.question},
        ]
        try:
            for _round in range(MAX_TOOL_ROUNDS):
                msg = await self.ai_client.chat_with_tools(
                    messages, tools=CHAT_TOOLS, temperature=self.temperature
                )
                tool_calls = getattr(msg, "tool_calls", None)
                if not tool_calls:
                    result.answer = getattr(msg, "content", "") or ""
                    break

                messages.append({
                    "role": "assistant",
                    "content": getattr(msg, "content", None),
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                })
                for tc in tool_calls:
                    try:
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    except json.JSONDecodeError:
                        args = {}
                    result.tool_calls.append((tc.function.name, args))
                    tool_result = case.tool_data.get(tc.function.name, DEFAULT_TOOL_MISSING)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_result,
                    })
            else:
                result.error = "超过最大工具轮次仍未给出回答"
        except Exception as e:  # noqa: BLE001 - phép chấm điểm ghi nhận mọi ngoại lệ khi chạy
            result.error = f"运行异常: {e}"
        return result


def _param_match(actual, expected) -> bool:
    """参数断言：expected 可为期望值或校验函数。"""
    if callable(expected):
        try:
            return bool(expected(actual))
        except Exception:
            return False
    return str(actual or "").strip() == str(expected)


def evaluate_case(case: ChatEvalCase, result: ChatEvalResult) -> list[str]:
    """对一次运行做规则断言，返回失败原因列表（空列表即通过）。"""
    failures: list[str] = []
    if result.error:
        failures.append(result.error)

    called = [name for name, _ in result.tool_calls]
    called_set = set(called)

    # 1) Danh sách trắng hành động: gọi công cụ chưa đăng ký là trượt luôn
    for name in sorted(called_set - TOOL_WHITELIST):
        failures.append(f"调用了白名单外的工具: {name}")

    # 2) Chọn công cụ
    if case.expect_no_tools and called:
        failures.append(f"不该调用工具却调用了: {called}")
    for name in case.expected_tools:
        if name not in called_set:
            failures.append(f"缺少必需的工具调用: {name}")
    for name in case.forbidden_tools:
        if name in called_set:
            failures.append(f"调用了不该调用的工具: {name}")

    # 3) Tham số công cụ
    for tool_name, expects in (case.param_checks or {}).items():
        calls = [args for name, args in result.tool_calls if name == tool_name]
        if not calls:
            continue  # Việc thiếu lời gọi đã được báo ở trên
        matched = any(
            all(_param_match(args.get(k), v) for k, v in expects.items())
            for args in calls
        )
        if not matched:
            expect_desc = {k: (v if not callable(v) else "<校验函数>") for k, v in expects.items()}
            failures.append(f"{tool_name} 参数不符合预期 {expect_desc}，实际 {calls}")

    # 4) Tính có căn cứ / ràng buộc nội dung
    answer = result.answer or ""
    for token in case.answer_must_contain:
        if token not in answer:
            failures.append(f"答案缺少工具结果引用: {token!r}")
    if case.answer_must_contain_any and not any(
        token in answer for token in case.answer_must_contain_any
    ):
        failures.append(f"答案未包含任一预期表述: {case.answer_must_contain_any}")
    for token in case.answer_must_not_contain:
        if token in answer:
            failures.append(f"答案包含不应出现的内容: {token!r}")

    return failures
