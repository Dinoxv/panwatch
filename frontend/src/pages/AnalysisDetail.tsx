import { useEffect, useState, type ReactNode } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  ArrowLeft,
  FileDown,
  ImageDown,
  List,
  ChevronDown,
  Target,
  TrendingUp,
  MessageSquare,
  Newspaper,
  BarChart3,
  Scale,
  ShieldAlert,
  History,
  type LucideIcon,
} from 'lucide-react'
import {
  tradingAgentsApi,
  type DeepAnalysisResult,
  type HistoryComparisonResponse,
} from '@panwatch/api'
import { Switch } from '@panwatch/base-ui/components/ui/switch'
import { buildAnalysisSections } from '@panwatch/biz-ui/analysis-sections'
import ShareCardModal from '../components/ShareCardModal'

const DECISION_COLOR: Record<string, string> = {
  buy: 'text-rose-500',
  hold: 'text-amber-500',
  sell: 'text-emerald-500',
}

/** Biểu tượng cho từng section (quyết định/kỹ thuật/tâm lý/tin tức/cơ bản/tranh luận/kiểm soát rủi ro), khớp id của buildAnalysisSections */
const SECTION_ICON: Record<string, LucideIcon> = {
  decision: Target,
  market: TrendingUp,
  social: MessageSquare,
  news: Newspaper,
  fundamentals: BarChart3,
  debate: Scale,
  risk: ShieldAlert,
}

/** Khóa localStorage cho công tắc hiện mục lục cấp hai (nhớ lựa chọn của người dùng) */
const TOC_SUB_KEY = 'panwatch_toc_show_sub'

/** Đoán thô thị trường từ mã: 6 chữ số = cổ phiếu A, 5 chữ số = cổ phiếu HK, còn lại = cổ phiếu Mỹ */
function inferMarket(symbol: string): string {
  if (/^\d{6}$/.test(symbol)) return 'CN'
  if (/^\d{5}$/.test(symbol)) return 'HK'
  return 'US'
}

function pctClass(v: number | null | undefined): string {
  if (v == null) return 'text-muted-foreground'
  return v > 0 ? 'text-rose-500' : v < 0 ? 'text-emerald-500' : 'text-muted-foreground'
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return '-'
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}

/** Tiêu đề → slug neo (bỏ nhấn mạnh markdown/dấu thăng/emoji, khoảng trắng thành gạch nối).
 *  Lúc đọc mục lục và lúc dựng tiêu đề đều dùng chung logic này, để id khớp nhau và bấm là nhảy được. */
function slugify(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[*_`#~]/g, '')
    .replace(/\s+/g, '-')
    .replace(/[^\w一-龥-]/g, '')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
}

/** Lấy đệ quy văn bản thuần từ children của nút tiêu đề ReactMarkdown (để tính id neo). */
function nodeText(children: ReactNode): string {
  if (typeof children === 'string') return children
  if (typeof children === 'number') return String(children)
  if (Array.isArray(children)) return children.map(nodeText).join('')
  if (children && typeof children === 'object' && 'props' in children) {
    return nodeText((children as { props?: { children?: ReactNode } }).props?.children)
  }
  return ''
}

/** Rút các tiêu đề cấp 2~4 trong một đoạn markdown (dùng cho mục lục cấp hai). */
function parseHeadings(markdown: string): { text: string; slug: string }[] {
  const out: { text: string; slug: string }[] = []
  for (const raw of markdown.split('\n')) {
    const m = /^(#{2,4})\s+(.+?)\s*#*$/.exec(raw)
    if (!m) continue
    const text = m[2].replace(/[*_`]/g, '').trim()
    if (text) out.push({ text, slug: slugify(m[2]) })
  }
  return out
}

export default function AnalysisDetailPage() {
  const { symbol = '', date = '' } = useParams()
  const navigate = useNavigate()
  const [result, setResult] = useState<DeepAnalysisResult | null>(null)
  const [history, setHistory] = useState<HistoryComparisonResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [activeId, setActiveId] = useState('')
  const [tocOpen, setTocOpen] = useState(false)
  const [showSub, setShowSub] = useState(() => {
    try {
      return localStorage.getItem(TOC_SUB_KEY) !== '0'
    } catch {
      return true
    }
  })
  const [pdfBusy, setPdfBusy] = useState(false)
  const [shareOpen, setShareOpen] = useState(false)

  const handleExportPdf = async () => {
    if (pdfBusy) return
    setPdfBusy(true)
    try {
      await tradingAgentsApi.downloadAnalysisPdf(symbol, date)
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Xuất thất bại')
    } finally {
      setPdfBusy(false)
    }
  }

  useEffect(() => {
    setLoading(true)
    tradingAgentsApi
      .getAnalysisByDate(symbol, date)
      .then(setResult)
      .catch(() => setResult(null))
      .finally(() => setLoading(false))
    tradingAgentsApi
      .getHistoryComparison(symbol, inferMarket(symbol), 90)
      .then(setHistory)
      .catch(() => setHistory(null))
  }, [symbol, date])

  // Nhớ công tắc mục lục cấp hai
  useEffect(() => {
    try {
      localStorage.setItem(TOC_SUB_KEY, showSub ? '1' : '0')
    } catch {
      /* ignore */
    }
  }, [showSub])

  const rawData = (result?.raw_data || {}) as Partial<DeepAnalysisResult['raw_data']>
  const sug = rawData.suggestion
  const reviewRequired = sug?.review_required === true || sug?.rating_raw === 'review'
  const decisionLabel = reviewRequired ? 'Chờ người rà soát' : sug?.action_label
  const decisionColor = reviewRequired ? 'text-orange-500' : (sug ? DECISION_COLOR[sug.action] || '' : '')
  const sections = buildAnalysisSections(rawData)
  const stats = history?.stats
  const items = history?.items || []

  // Mục lục đầy đủ: mỗi section (cấp một) + các tiêu đề cấp 2~4 trong markdown của nó (cấp hai) + đối chiếu quyết định lịch sử
  const fullToc: { id: string; title: string; level: 0 | 1 }[] = []
  for (const s of sections) {
    fullToc.push({ id: `sec-${s.id}`, title: s.title, level: 0 })
    for (const h of parseHeadings(s.markdown)) {
      fullToc.push({ id: `h-${s.id}-${h.slug}`, title: h.text, level: 1 })
    }
  }
  fullToc.push({ id: 'sec-history', title: 'Đối chiếu quyết định lịch sử', level: 0 })
  // Công tắc quyết định có hiện/liên động mục lục cấp hai hay không
  const toc = showSub ? fullToc : fullToc.filter((t) => t.level === 0)

  // Liên động khi cuộn: cuộn nội dung thì tự tô sáng đoạn hiện tại (lấy tiêu đề cao nhất trong khung nhìn, tránh thanh điều hướng ở đỉnh)
  useEffect(() => {
    if (!result) return
    const els = toc
      .map((t) => document.getElementById(t.id))
      .filter((el): el is HTMLElement => !!el)
    if (!els.length) return
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
        if (visible[0]) setActiveId(visible[0].target.id)
      },
      { rootMargin: '-100px 0px -55% 0px', threshold: 0 },
    )
    els.forEach((el) => observer.observe(el))
    return () => observer.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result, toc.length])

  if (loading) {
    return <div className="p-12 text-center text-muted-foreground">Đang tải...</div>
  }
  if (!result) {
    return (
      <div className="p-12 text-center text-muted-foreground space-y-3">
        <div>Không tìm thấy bản ghi phân tích chuyên sâu của {symbol} ngày {date} </div>
        <button onClick={() => navigate(-1)} className="text-primary hover:underline">
          Quay lại
        </button>
      </div>
    )
  }

  const scrollTo = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
  }

  // Tiêu đề đoạn đang đọc (hiện trên thanh gập ở di động, để người dùng biết mình đang ở đâu)
  const currentTitle = toc.find((t) => t.id === activeId)?.title || ''

  // Dựng tiêu đề markdown: gắn id neo trùng với mục lục + chừa khoảng trên (tránh thanh điều hướng dính đỉnh)
  const headingComponents = (sectionId: string) => {
    const make = (Tag: 'h2' | 'h3' | 'h4') =>
      function Heading({ children }: { children?: ReactNode }) {
        const id = `h-${sectionId}-${slugify(nodeText(children))}`
        return (
          <Tag id={id} className="scroll-mt-24">
            {children}
          </Tag>
        )
      }
    return { h2: make('h2'), h3: make('h3'), h4: make('h4') }
  }

  // Đầu mục lục (tiêu đề + công tắc mục lục cấp hai), dùng chung cho cột phải desktop / xổ xuống di động
  const tocHeader = (
    <div className="flex items-center justify-between gap-2 mb-2 px-2">
      <span className="text-[11px] font-medium text-muted-foreground/70">Mục lục</span>
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <span className="cursor-pointer select-none" onClick={() => setShowSub((v) => !v)}>
          Mục lục cấp hai
        </span>
        <Switch checked={showSub} onCheckedChange={setShowSub} />
      </div>
    </div>
  )

  // Danh sách mục lục (dùng chung cột phải desktop / xổ xuống di động); onAfter để di động chọn xong tự thu lại
  const tocNav = (onAfter?: () => void) => (
    <nav className="space-y-0.5 text-[13px]">
      {toc.map((t) => (
        <button
          key={t.id}
          onClick={() => {
            scrollTo(t.id)
            onAfter?.()
          }}
          className={`block w-full text-left py-1 rounded-md transition-colors truncate ${
            t.level === 1 ? 'pl-5 pr-2 text-[12px]' : 'px-2'
          } ${
            activeId === t.id
              ? 'bg-accent text-foreground font-medium'
              : 'text-muted-foreground hover:text-foreground hover:bg-accent/50'
          }`}
        >
          {t.title}
        </button>
      ))}
    </nav>
  )

  return (
    <div className="min-h-screen">
      <div className="max-w-5xl mx-auto px-4 pb-12 flex gap-8">
        {/* Cột trái: thanh tiêu đề + nội dung (thanh tiêu đề chỉ chiếm bề ngang cột trái, không đè lên mục lục bên phải) */}
        <div className="flex-1 min-w-0 max-w-3xl">
          {/* Thanh trên cùng */}
          <div className="border-b border-border/40 pb-3 mb-4 flex items-center gap-3">
            <button
              onClick={() => navigate(-1)}
              className="w-8 h-8 rounded-lg flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-accent transition-all shrink-0"
              aria-label="Quay lại"
            >
              <ArrowLeft className="w-4 h-4" />
            </button>
            <h1 className="text-base font-bold truncate min-w-0">{result.title || `Phân tích chuyên sâu ${symbol}`}</h1>
            <span className="text-[12px] text-muted-foreground shrink-0">{date}</span>
            <button
              onClick={() => setShareOpen(true)}
              className="ml-auto shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border/50 text-[12.5px] text-muted-foreground hover:text-foreground hover:bg-accent transition-all"
              title="Dựng thẻ kết luận chia sẻ được"
            >
              <ImageDown className="w-3.5 h-3.5" />
              Ảnh chia sẻ
            </button>
            <button
              onClick={handleExportPdf}
              disabled={pdfBusy}
              className="shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border/50 text-[12.5px] text-muted-foreground hover:text-foreground hover:bg-accent transition-all disabled:opacity-50"
              title="Xuất tệp PDF"
            >
              <FileDown className="w-3.5 h-3.5" />
              {pdfBusy ? 'Đang xuất…' : 'Xuất PDF'}
            </button>
          </div>

          {/* Nội dung */}
          <article>
          {/* Tóm tắt quyết định (di động đặt ở đầu nội dung; desktop dời sang vùng mục lục bên phải, xem aside phía dưới) */}
          {sug && (
            <div className="lg:hidden rounded-xl bg-accent/30 p-4 mb-6 flex items-center gap-3 flex-wrap">
              <span className={`text-[24px] font-bold ${decisionColor}`}>
                {decisionLabel}
              </span>
              {reviewRequired && <span className="text-[12px] text-orange-600">Dữ liệu hoặc kết luận còn điểm chưa chắc chắn, xin kiểm chứng bằng tay rồi mới quyết</span>}
              <span className="text-[13px] text-muted-foreground">
                Độ tin cậy {sug.confidence?.toFixed(1) ?? '-'} / 10
              </span>
              <span className="ml-auto text-[11px] text-muted-foreground">
                Chi phí ${rawData.cost_usd?.toFixed(4) ?? '-'}
              </span>
            </div>
          )}

          {/* Mục lục di động: thanh gập dính đỉnh, hiện đoạn hiện tại, mở ra thành xổ xuống (dạng phủ lên), chọn xong/bấm ra ngoài thì thu lại (desktop ẩn) */}
          <div className="lg:hidden sticky top-16 z-30 mb-6">
            <div className="relative">
              <button
                onClick={() => setTocOpen((o) => !o)}
                className="w-full flex items-center gap-2 px-3.5 py-2.5 rounded-xl border border-border/50 bg-card/95 backdrop-blur text-[13px] font-medium shadow-sm"
              >
                <List className="w-4 h-4 shrink-0" />
                <span className="truncate">{currentTitle || 'Mục lục'}</span>
                <ChevronDown
                  className={`w-4 h-4 ml-auto shrink-0 transition-transform ${tocOpen ? 'rotate-180' : ''}`}
                />
              </button>
              {tocOpen && (
                <>
                  <div className="fixed inset-0 z-0" onClick={() => setTocOpen(false)} />
                  <div className="absolute left-0 right-0 top-full mt-1 z-10 rounded-xl border border-border/50 bg-card/95 backdrop-blur shadow-lg max-h-[60vh] overflow-y-auto scrollbar p-2">
                    {tocHeader}
                    {tocNav(() => setTocOpen(false))}
                  </div>
                </>
              )}
            </div>
          </div>

          {/* Bài dài của từng phần */}
          {sections.map((s) => {
            const Icon = SECTION_ICON[s.id]
            return (
              <section key={s.id} id={`sec-${s.id}`} className="mb-12 scroll-mt-24">
                <h2 className="flex items-center gap-2 text-[18px] font-bold mb-4 pb-2 border-b border-border/40">
                  {Icon && <Icon className="w-[18px] h-[18px] text-primary/70 shrink-0" />}
                  {s.title}
                </h2>
                <div className="prose prose-base dark:prose-invert max-w-none leading-relaxed prose-headings:mt-6 prose-headings:mb-2 prose-h2:text-[16px] prose-h3:text-[15px] prose-h4:text-[14px] prose-h2:font-semibold prose-h3:font-semibold prose-p:my-3 prose-p:text-foreground/90 prose-li:my-1 prose-table:my-4 prose-th:px-3 prose-th:py-2 prose-td:px-3 prose-td:py-2 prose-strong:text-foreground">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={headingComponents(s.id)}>
                    {s.markdown}
                  </ReactMarkdown>
                </div>
              </section>
            )
          })}

          {/* Đối chiếu quyết định lịch sử */}
          <section id="sec-history" className="mb-10 scroll-mt-24">
            <h2 className="flex items-center gap-2 text-[18px] font-bold mb-4 pb-2 border-b border-border/40">
              <History className="w-[18px] h-[18px] text-primary/70 shrink-0" />
              Quyết định lịch sử vs tăng giảm thực tế
            </h2>
            {stats && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4 text-[13px]">
                <div className="rounded-lg bg-accent/30 p-3">
                  <div className="text-[11px] text-muted-foreground mb-1">Tỷ lệ trúng chung</div>
                  <div className="font-bold">{stats.overall_hit_rate != null ? `${(stats.overall_hit_rate * 100).toFixed(0)}%` : '-'}</div>
                </div>
                <div className="rounded-lg bg-accent/30 p-3">
                  <div className="text-[11px] text-muted-foreground mb-1">Trúng lệnh mua</div>
                  <div className="font-bold">{stats.buy_hit_rate != null ? `${(stats.buy_hit_rate * 100).toFixed(0)}%` : '-'}</div>
                </div>
                <div className="rounded-lg bg-accent/30 p-3">
                  <div className="text-[11px] text-muted-foreground mb-1">Trúng lệnh bán</div>
                  <div className="font-bold">{stats.sell_hit_rate != null ? `${(stats.sell_hit_rate * 100).toFixed(0)}%` : '-'}</div>
                </div>
                <div className="rounded-lg bg-accent/30 p-3">
                  <div className="text-[11px] text-muted-foreground mb-1">Lợi nhuận bình quân 20 ngày</div>
                  <div className={`font-bold ${pctClass(stats.avg_return_20d_pct)}`}>{fmtPct(stats.avg_return_20d_pct)}</div>
                </div>
              </div>
            )}
            {items.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-[13px]">
                  <thead>
                    <tr className="border-b border-border text-muted-foreground text-[12px]">
                      <th className="text-left py-2 pr-3">Ngày</th>
                      <th className="text-left py-2 px-2">Quyết định</th>
                      <th className="text-right py-2 px-2">Giá lúc phân tích</th>
                      <th className="text-right py-2 px-2">1 ngày</th>
                      <th className="text-right py-2 px-2">5 ngày</th>
                      <th className="text-right py-2 px-2">20 ngày</th>
                      <th className="text-right py-2 pl-2">Trúng</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((it, i) => (
                      <tr key={i} className="border-b border-border/50">
                        <td className="py-2 pr-3">{it.analysis_date}</td>
                        <td className="py-2 px-2">{it.action_label}{it.confidence != null ? ` (${it.confidence.toFixed(1)})` : ''}</td>
                        <td className="text-right py-2 px-2">{it.price_at_analysis ?? '-'}</td>
                        <td className={`text-right py-2 px-2 ${pctClass(it.return_1d_pct)}`}>{fmtPct(it.return_1d_pct)}</td>
                        <td className={`text-right py-2 px-2 ${pctClass(it.return_5d_pct)}`}>{fmtPct(it.return_5d_pct)}</td>
                        <td className={`text-right py-2 px-2 ${pctClass(it.return_20d_pct)}`}>{fmtPct(it.return_20d_pct)}</td>
                        <td className="text-right py-2 pl-2">{it.hit_20d == null ? '-' : it.hit_20d ? '✓' : '✗'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-[13px] text-muted-foreground py-4">Chưa có bản ghi quyết định lịch sử</div>
            )}
          </section>

          {/* Miễn trừ trách nhiệm */}
          <div className="text-[11px] text-muted-foreground/70 italic border-t border-border/30 pt-4">
            Bản phân tích này do khung nhiều Agent AI dựng ra, chỉ để học hỏi nghiên cứu tham khảo, không phải khuyến nghị đầu tư. Đầu tư có rủi ro, quyết định phải tự cân nhắc.
          </div>
          </article>
        </div>

        {/* Cột phải: quyết định cuối + mục lục gộp chung một thẻ (bắt đầu ngang với tiêu đề, không bị tiêu đề đè; token giao diện hợp cả ngày lẫn đêm) */}
        <aside className="hidden lg:block w-52 shrink-0">
          <div className="sticky top-24 rounded-xl border border-border bg-card overflow-hidden">
            {/* Tóm tắt quyết định cuối */}
            {sug && (
              <div className="p-3.5 border-b border-border">
                <div className="flex items-baseline justify-between gap-2">
                  <span className={`text-[22px] font-bold leading-none ${decisionColor}`}>
                    {decisionLabel}
                  </span>
                  <span className="text-[11px] text-muted-foreground shrink-0">
                    ${rawData.cost_usd?.toFixed(4) ?? '-'}
                  </span>
                </div>
                {reviewRequired && (
                  <p className="mt-2 text-[11px] leading-4 text-orange-600">
                    Thượng nguồn không thể sinh xếp hạng chạy được một cách an toàn, xin kiểm chứng dữ liệu và báo cáo bằng tay.
                  </p>
                )}
                {sug.confidence != null && (
                  <div className="mt-2.5">
                    <div className="flex items-center justify-between text-[11px] text-muted-foreground mb-1">
                      <span>Độ tin cậy</span>
                      <span className="font-medium text-foreground">{sug.confidence.toFixed(1)} / 10</span>
                    </div>
                    <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                      <div
                        className={`h-full rounded-full ${
                          sug.action === 'buy'
                            ? 'bg-rose-500'
                            : sug.action === 'sell'
                              ? 'bg-emerald-500'
                              : 'bg-amber-500'
                        }`}
                        style={{ width: `${Math.max(0, Math.min(100, sug.confidence * 10))}%` }}
                      />
                    </div>
                  </div>
                )}
              </div>
            )}
            {/* Mục lục */}
            <div className="p-2">
              {tocHeader}
              <div className="max-h-[calc(100vh-19rem)] overflow-y-auto scrollbar">{tocNav()}</div>
            </div>
          </div>
        </aside>
      </div>

      {/* Thẻ chia sẻ (xuất PNG) */}
      <ShareCardModal
        open={shareOpen}
        onClose={() => setShareOpen(false)}
        result={result}
        symbol={symbol}
        date={date}
      />
    </div>
  )
}
