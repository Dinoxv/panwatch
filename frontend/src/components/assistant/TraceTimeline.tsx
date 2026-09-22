import { AlertCircle, CheckCircle2, ChevronDown, FileClock, Gauge, ListTree, PauseCircle, Search, Wrench } from 'lucide-react'
import { useState } from 'react'
import type { AssistantTraceEvent } from '@panwatch/api'

interface TraceTimelineProps {
  events: AssistantTraceEvent[]
  live?: boolean
}

function describeExtensionEvent(event: AssistantTraceEvent): { label: string; icon: typeof FileClock } | null {
  if (event.data.extension !== 'tool_research') return null
  const data = event.data.data || {}
  switch (event.data.event) {
    case 'started': return { label: 'Rà công cụ khả dụng', icon: Search }
    case 'exposure': return {
      label: `Danh mục công cụ đã sẵn sàng: ${data.direct_tools?.length || 0} công cụ dùng thẳng, ${data.loaded_tools?.length || 0} công cụ đã nạp`,
      icon: Search,
    }
    case 'candidates_scored': return { label: `Lọc công cụ ứng viên: ${data.candidates?.length || 0}`, icon: Search }
    case 'completed': return { label: `Rà công cụ xong: chọn được ${data.selected_tools?.length || 0}`, icon: Search }
    case 'searched': return { label: `Tìm công cụ xong: nạp ${data.selected_tools?.length || 0}`, icon: Search }
    case 'fallback': return { label: 'Rà công cụ thất bại, tiếp tục dùng bộ công cụ mặc định', icon: AlertCircle }
    default: return { label: `Sự kiện mở rộng: ${event.data.event || 'unknown'}`, icon: FileClock }
  }
}

function describe(event: AssistantTraceEvent): { label: string; icon: typeof FileClock } {
  const name = typeof event.data.name === 'string' ? event.data.name : ''
  const extension = event.event === 'extension_event' ? describeExtensionEvent(event) : null
  if (extension) return extension
  switch (event.event) {
    case 'context_prepared': return { label: event.data.compressed ? 'Ngữ cảnh đã nén và sẵn sàng' : 'Ngữ cảnh đã sẵn sàng', icon: FileClock }
    case 'step_updated': return { label: `Chạy bước ${event.data.step || ''}`, icon: ListTree }
    case 'tool_call_start': return { label: `Gọi công cụ: ${name}`, icon: Wrench }
    case 'tool_result': return { label: event.data.ok ? `Công cụ xong: ${name}` : `Công cụ hỏng: ${name}`, icon: event.data.ok ? CheckCircle2 : AlertCircle }
    case 'model_usage': return { label: `Mức dùng mô hình: vào ${event.data.input_tokens || 0}, ra ${event.data.output_tokens || 0}`, icon: Gauge }
    case 'approval_required': return { label: 'Chờ người dùng duyệt', icon: PauseCircle }
    case 'paused': return { label: 'Tác vụ đã tạm dừng', icon: PauseCircle }
    case 'done': return { label: 'Tác vụ hoàn tất', icon: CheckCircle2 }
    case 'error': return { label: 'Tác vụ thất bại', icon: AlertCircle }
    default: return { label: 'Tác vụ đã khởi chạy', icon: FileClock }
  }
}

function detail(event: AssistantTraceEvent): string {
  if (event.event === 'tool_call_start' && event.data.arguments) {
    return JSON.stringify(event.data.arguments)
  }
  if (event.event === 'tool_result' && typeof event.data.preview === 'string') {
    return event.data.preview
  }
  return ''
}

function summary(events: AssistantTraceEvent[]): string {
  const toolCalls = events.filter((event) => event.event === 'tool_call_start').length
  const latest = [...events].reverse().find((event) => ['done', 'error', 'paused'].includes(event.event))
  const status = latest?.event === 'done'
    ? 'Đã xong'
    : latest?.event === 'error'
    ? 'Đã hỏng'
    : latest?.event === 'paused'
    ? 'Chờ chạy tiếp'
    : 'Đang chạy'
  return toolCalls > 0 ? `${status} · ${toolCalls} lượt gọi công cụ` : status
}

export function TraceTimeline({ events, live = false }: TraceTimelineProps) {
  const [expanded, setExpanded] = useState(live)
  if (events.length === 0) return null
  return (
    <section data-testid="assistant-trace" className="rounded-lg border border-border/50 bg-background/70 px-3 py-2 text-[11px]">
      <button
        type="button"
        className="flex w-full items-center gap-1.5 text-left font-medium text-muted-foreground hover:text-foreground"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <FileClock className="h-3.5 w-3.5 shrink-0" />
        <span>Nhật ký thực thi</span>
        <span className="min-w-0 flex-1 truncate text-[10px] font-normal">{summary(events)}</span>
        <ChevronDown className={`h-3.5 w-3.5 shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </button>
      {expanded && (
        <ol className="mt-2 space-y-1.5 border-t border-border/40 pt-2">
          {events.map((event, index) => {
            const { label, icon: Icon } = describe(event)
            const eventDetail = detail(event)
            return (
              <li key={`${event.id ?? index}-${event.event}-${index}`} className="flex items-start gap-2 text-foreground">
                <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <div className="min-w-0">
                  <div>{label}</div>
                  {eventDetail && <div className="break-words text-muted-foreground">{eventDetail}</div>}
                </div>
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
