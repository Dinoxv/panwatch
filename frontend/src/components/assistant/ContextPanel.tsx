import { Check, Loader2, Minimize2, X } from 'lucide-react'
import type { AssistantContextDetail, AssistantContextSnapshot, ContextUsage } from '@panwatch/api'

interface ContextPanelProps {
  detail: AssistantContextDetail | null
  loading: boolean
  compressing: boolean
  error?: string
  onCompress: (mode: AssistantContextSnapshot['mode']) => void
  onClose?: () => void
}

const SECTION_LABELS: Record<string, string> = {
  system: 'Chỉ dẫn hệ thống',
  summary: 'Tóm tắt có cấu trúc',
  page_context: 'Ngữ cảnh trang',
  tool_definitions: 'Định nghĩa công cụ',
  history: 'Tin nhắn lịch sử',
  recent_messages: 'Tin nhắn gần nhất',
}

const STATUS_LABELS = {
  not_needed: 'Lần này chưa cần nén',
  compressed: 'Nén xong',
  no_gain: 'Lần nén này không lợi gì, đã giữ nguyên ngữ cảnh cũ',
} as const

const MODE_LABELS: Array<{ mode: AssistantContextSnapshot['mode']; label: string }> = [
  { mode: 'balanced', label: 'Nén cân bằng' },
  { mode: 'preserve_details', label: 'Giữ chi tiết rồi nén' },
  { mode: 'handoff', label: 'Gom thành bản bàn giao' },
]

function usagePercent(usage: ContextUsage): number {
  return Math.min(100, Math.round((usage.total_tokens / usage.budget_tokens) * 100))
}

function usageLabel(usage: ContextUsage): string {
  if (usage.measurement === 'provider') return 'Token vào thực tế'
  if (usage.measurement === 'tokenizer') return 'Token vào theo Tokenizer'
  return 'Token vào ước tính'
}

export function ContextPanel({ detail, loading, compressing, error, onCompress, onClose }: ContextPanelProps) {
  return (
    <section data-testid="assistant-context-panel" className="border-b border-border/40 bg-background px-4 py-3 text-[12px]">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-medium text-foreground">Mức dùng ngữ cảnh</h3>
          <p className="mt-0.5 text-muted-foreground">Chỉ ảnh hưởng lần chạy kế tiếp, không xóa tin nhắn trong phiên.</p>
        </div>
        {onClose && (
          <button type="button" onClick={onClose} className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground" aria-label="Đóng bảng ngữ cảnh">
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {loading && <div className="py-5 text-muted-foreground">Đang đo ngữ cảnh…</div>}
      {!loading && detail && (
        <>
          <div className="mt-3 flex items-center justify-between gap-3">
            <span className="font-medium tabular-nums">{usageLabel(detail.usage)}: {detail.usage.total_tokens.toLocaleString()} / {detail.usage.budget_tokens.toLocaleString()}</span>
            <span className={detail.status === 'needs_compression' ? 'text-rose-600' : detail.status === 'warning' ? 'text-amber-600' : 'text-emerald-600'}>
              {detail.status === 'needs_compression' ? 'Cần nén bớt' : detail.status === 'warning' ? 'Sắp chạm trần' : 'Bình thường'} · {usagePercent(detail.usage)}%
            </span>
          </div>
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-muted">
            <div
              className={detail.status === 'needs_compression' ? 'h-full bg-rose-500' : detail.status === 'warning' ? 'h-full bg-amber-500' : 'h-full bg-emerald-500'}
              style={{ width: `${usagePercent(detail.usage)}%` }}
            />
          </div>
          <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1.5">
            {detail.usage.sections.filter((section) => section.tokens > 0).map((section) => (
              <div key={section.name} className="flex min-w-0 items-center justify-between gap-2 text-muted-foreground">
                <span className="truncate">{SECTION_LABELS[section.name] || section.name}</span>
                <span className="shrink-0 tabular-nums text-foreground">{section.tokens.toLocaleString()}</span>
              </div>
            ))}
          </div>
          {detail.snapshot && (
            <div className="mt-3 border-t border-border/40 pt-2 text-muted-foreground">
              <div className="flex items-center gap-1.5"><Check className="h-3.5 w-3.5 text-emerald-600" />Tóm tắt v{detail.snapshot.version}</div>
              {detail.snapshot.summary.goal.length > 0 && <p className="mt-1 truncate">Mục tiêu:{detail.snapshot.summary.goal[0]}</p>}
              {detail.snapshot.summary.current_state && <p className="truncate">Trạng thái:{detail.snapshot.summary.current_state}</p>}
              {detail.snapshot.summary.open_items.length > 0 && <p className="truncate">Việc còn treo:{detail.snapshot.summary.open_items[0]}</p>}
            </div>
          )}
          {detail.last_compression && (
            <div className="mt-3 border-t border-border/40 pt-2 text-muted-foreground">
              <div className={detail.last_compression.status === 'no_gain' ? 'text-amber-600' : 'text-emerald-600'}>
                {STATUS_LABELS[detail.last_compression.status]}
              </div>
              {detail.last_compression.status === 'compressed' && (
                <p className="mt-1">
                  {detail.last_compression.usage_before.total_tokens.toLocaleString()} → {detail.last_compression.usage_after.total_tokens.toLocaleString()}, tiết kiệm {detail.last_compression.saved_tokens.toLocaleString()} Token ({detail.last_compression.saved_percent}%)
                </p>
              )}
            </div>
          )}
          <div className="mt-3 flex flex-wrap gap-1.5">
            {MODE_LABELS.map(({ mode, label }) => (
              <button
                key={mode}
                type="button"
                disabled={compressing}
                onClick={() => onCompress(mode)}
                className="inline-flex items-center gap-1 rounded-md border border-border bg-background px-2 py-1.5 text-[11px] text-foreground transition-colors hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
              >
                {compressing && mode === 'balanced' ? <Loader2 className="h-3 w-3 animate-spin" /> : <Minimize2 className="h-3 w-3" />}
                {compressing && mode === 'balanced' ? 'Đang nén…' : label}
              </button>
            ))}
          </div>
        </>
      )}
      {!loading && !detail && <p className="py-4 text-muted-foreground">Hiện chưa có phiên nào để đo.</p>}
      {error && <p className="mt-2 text-rose-600">{error}</p>}
    </section>
  )
}
