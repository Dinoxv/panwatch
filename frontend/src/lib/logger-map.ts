// Map Python module logger names to concise Chinese display names
export const LOGGER_MAPPING: Record<string, string> = {
  // Agents
  'src.agents.daily_report': 'Ôn lại sau phiên',
  'src.agents.premarket_outlook': 'Phân tích trước phiên',
  'src.agents.intraday_monitor': 'Theo dõi trong phiên',
  'src.agents.base': 'Chuỗi thực thi Agent',
  'src.agents.news_digest': 'Tin nhanh',
  'src.agents.chart_analyst': 'Phân tích kỹ thuật',
  'src.agents.tradingagents': 'Phân tích chuyên sâu',
  'src.agents.tradingagents.agent': 'Phân tích chuyên sâu - luồng chính',
  'src.agents.tradingagents.observability': 'Phân tích chuyên sâu - tiến độ và chi phí',
  'src.agents.tradingagents.data_context': 'Phân tích chuyên sâu - ngữ cảnh dữ liệu',
  'src.agents.tradingagents.toolkit_adapter': 'Phân tích chuyên sâu - khớp nối dữ liệu',
  'src.agents.tradingagents.decision': 'Phân tích chuyên sâu - quyết định và mô phỏng',
  'src.agents.tradingagents.runtime_support': 'Phân tích chuyên sâu - tương thích lúc chạy',
  'src.agents.tradingagents.operations': 'Phân tích chuyên sâu - kích hoạt và hậu kiểm',
  'tradingagents': 'Phân tích chuyên sâu (thượng nguồn)',

  // Core
  'src.core.scheduler': 'Bộ lập lịch',
  'src.core.ai_client': 'Máy khách AI',
  'src.core.notifier': 'Thông báo',
  'src.core.analysis_history': 'Lịch sử phân tích',
  'src.core.suggestion_pool': 'Kho khuyến nghị',
  'src.core.data_collector': 'Thu thập dữ liệu',

  // Collectors
  'src.collectors.akshare_collector': 'Thu thập bảng giá',
  'src.collectors.kline_collector': 'Thu thập nến',
  'src.collectors.capital_flow_collector': 'Thu thập dòng tiền',
  'src.collectors.news_collector': 'Thu thập tin tức',
  'src.collectors.screenshot_collector': 'Thu thập ảnh chụp màn hình',

  // Web/API
  'src.web.api': 'API',
  'src.web.app': 'Ứng dụng Web',
  'src.web.database': 'Cơ sở dữ liệu',
  'src.web.stock_list': 'Danh sách mã',
  'api': 'API',

  // Entry
  'server': 'Dịch vụ',

  // Third-party & infra
  'httpx': 'Máy khách HTTP',
  'httpcore': 'Lõi HTTP',
  'urllib3': 'Thư viện HTTP',
  'requests': 'Máy khách HTTP',
  'uvicorn.access': 'Nhật ký truy cập',
  'uvicorn.error': 'Lỗi Uvicorn',
  'uvicorn': 'Uvicorn',
  'fastapi': 'FastAPI',
  'starlette': 'Starlette',
  'sqlalchemy.engine': 'Engine cơ sở dữ liệu',
  'sqlalchemy': 'SQLAlchemy',
  'apscheduler': 'APScheduler',
  'playwright': 'Trình duyệt',
  'openai': 'AI SDK',
  'tenacity': 'Thư viện thử lại',
}

export function mapLoggerName(moduleName?: string): string {
  if (!moduleName) return ''
  let bestKey = ''
  for (const key of Object.keys(LOGGER_MAPPING)) {
    if (moduleName === key || moduleName.startsWith(key)) {
      if (key.length > bestKey.length) bestKey = key
    }
  }
  return LOGGER_MAPPING[bestKey] || moduleName
}

export function loggerOptions(): { key: string, label: string }[] {
  return Object.entries(LOGGER_MAPPING).map(([key, label]) => ({ key, label }))
}
