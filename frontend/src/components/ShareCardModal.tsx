import { type DeepAnalysisResult } from '@panwatch/api'
import { normalizeSuggestionAction } from '@panwatch/biz-ui/components/suggestion-action'
import ShareCardDialog from './ShareCardDialog'

interface ShareCardModalProps {
  open: boolean
  onClose: () => void
  result: DeepAnalysisResult
  symbol: string
  date: string
}

/**
 * Thang 5 bậc → nhãn hiển thị + bảng màu thị trường A (đỏ tăng, xanh giảm).
 * Tái dùng phép chuẩn hóa của technical-badge / suggestion-action: mua vào và
 * tăng tỷ trọng = đỏ (nhìn lên), bán ra và hạ tỷ trọng = xanh (nhìn xuống),
 * nắm giữ = hổ phách (trung tính).
 * Ở đây dùng mã màu thập lục phân tường minh và tự chứa, để ảnh PNG xuất ra
 * vẫn đúng màu ở mọi giao diện (sáng hoặc tối).
 */
const RATING_VISUAL: Record<
  string,
  { label: string; color: string; soft: string; gradFrom: string; gradTo: string }
> = {
  // Nhìn lên (đỏ)
  buy: { label: 'Mua vào', color: '#e11d48', soft: '#fff1f2', gradFrom: '#fb7185', gradTo: '#e11d48' },
  add: { label: 'Tăng tỷ trọng', color: '#e11d48', soft: '#fff1f2', gradFrom: '#fda4af', gradTo: '#e11d48' },
  // Trung tính (hổ phách)
  hold: { label: 'Nắm giữ', color: '#d97706', soft: '#fffbeb', gradFrom: '#fbbf24', gradTo: '#d97706' },
  // Nhìn xuống (xanh)
  reduce: { label: 'Hạ tỷ trọng', color: '#059669', soft: '#ecfdf5', gradFrom: '#34d399', gradTo: '#059669' },
  sell: { label: 'Bán ra', color: '#059669', soft: '#ecfdf5', gradFrom: '#6ee7b7', gradTo: '#059669' },
}
const RATING_FALLBACK = {
  label: 'Quan sát',
  color: '#475569',
  soft: '#f8fafc',
  gradFrom: '#94a3b8',
  gradTo: '#475569',
}
const REVIEW_VISUAL = {
  label: 'Chờ người rà soát',
  color: '#c2410c',
  soft: '#fff7ed',
  gradFrom: '#fb923c',
  gradTo: '#c2410c',
}

/** Ánh xạ các giá trị gốc thang 5 bậc mà máy chủ có thể trả về (overweight/underweight) sang từ mà bộ chuẩn hóa nhận ra. */
function mapRatingRaw(raw?: string): string | undefined {
  if (!raw) return undefined
  const r = raw.toLowerCase().trim()
  if (r === 'overweight') return 'add'
  if (r === 'underweight') return 'reduce'
  return r
}

/**
 * Bóc tên và mã cổ phiếu từ tiêu đề: bỏ nhãn trong ngoặc vuông ở đầu như
 * 【深度】, bỏ phần «: xếp hạng» ở cuối.
 * Ví dụ: 「【深度】广汽集团(601238):持有」 → 「广汽集团(601238)」
 */
function parseStockName(title: string, symbol: string): string {
  let s = (title || '').trim()
  s = s.replace(/^【[^】]*】\s*/, '') // Bỏ nhãn 【...】 đầu tiên ở đầu chuỗi
  s = s.replace(/[:：]\s*[^:：]*$/, '') // Bỏ phần 「:xxx」 ở cuối (xếp hạng)
  s = s.trim()
  return s || symbol
}

/**
 * Làm sạch kết luận thành một đoạn: bỏ dấu ** in đậm của markdown, rồi bỏ tiền
 * tố 「Action: x Reasoning:」 ở đầu.
 * Khoảng trắng thừa nén về một dấu cách, để line-clamp hiển thị gọn.
 */
function cleanConclusion(text: string): string {
  let s = (text || '').replace(/\*\*/g, '')
  s = s.replace(/^Action\s*[:：]\s*\S+\s*Reasoning\s*[:：]\s*/i, '')
  s = s.replace(/\s+/g, ' ').trim()
  return s
}

export default function ShareCardModal({ open, onClose, result, symbol, date }: ShareCardModalProps) {
  const sug = result.raw_data?.suggestion
  // Nguồn xếp hạng: ưu tiên giá trị gốc thang 5 bậc từ máy chủ, không có thì dùng action, cuối cùng mới lấy action_label làm dự phòng.
  const ratingRaw = mapRatingRaw(sug?.rating_raw)
  const normalized = normalizeSuggestionAction(ratingRaw || sug?.action, sug?.action_label)
  const reviewRequired = sug?.review_required === true || sug?.rating_raw === 'review'
  const visual = reviewRequired ? REVIEW_VISUAL : (normalized && RATING_VISUAL[normalized]) || RATING_FALLBACK

  const stockName = parseStockName(result.title || '', symbol)
  const confidence = sug?.confidence
  const costUsd = result.raw_data?.cost_usd
  const conclusion = cleanConclusion(sug?.signal || sug?.reason || '')
  const confPct = Math.max(0, Math.min(100, (confidence ?? 0) * 10))

  return (
    <ShareCardDialog open={open} onClose={onClose} filename={`${stockName}-${date}-the-phan-tich`}>
      {/* Phần đầu: tên và mã cổ phiếu / ngày */}
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          gap: 12,
        }}
      >
        <div style={{ fontSize: 24, fontWeight: 800, lineHeight: 1.2, color: '#0f172a' }}>
          {stockName}
        </div>
        <div style={{ fontSize: 14, color: '#94a3b8', fontWeight: 500, flexShrink: 0 }}>{date}</div>
      </div>

      {/* Khối đầu: xếp hạng lớn + thanh độ tin cậy + chi phí */}
      <div
        style={{
          marginTop: 20,
          borderRadius: 18,
          padding: '22px 24px',
          background: `linear-gradient(135deg, ${visual.gradFrom} 0%, ${visual.gradTo} 100%)`,
          color: '#ffffff',
          boxShadow: `0 10px 30px -8px ${visual.color}66`,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div
            style={{
              fontSize: 13,
              fontWeight: 600,
              letterSpacing: 1,
              opacity: 0.92,
              flexShrink: 0,
            }}
          >
            AI 投研结论
          </div>
          <div
            style={{
              fontSize: 42,
              fontWeight: 900,
              lineHeight: 1,
              letterSpacing: 2,
              marginLeft: 'auto',
            }}
          >
            {visual.label}
          </div>
        </div>

        {/* Thanh độ tin cậy */}
        <div style={{ marginTop: 18 }}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: 12.5,
              opacity: 0.92,
              marginBottom: 6,
            }}
          >
            <span>Độ tin cậy</span>
            <span style={{ fontWeight: 700 }}>
              {confidence != null ? confidence.toFixed(1) : '-'} / 10
            </span>
          </div>
          <div
            style={{
              height: 8,
              borderRadius: 999,
              background: 'rgba(255,255,255,0.3)',
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                height: '100%',
                width: `${confPct}%`,
                borderRadius: 999,
                background: '#ffffff',
              }}
            />
          </div>
          <div style={{ marginTop: 10, fontSize: 12, opacity: 0.85 }}>
            分析成本 ${costUsd != null ? costUsd.toFixed(4) : '-'}
          </div>
        </div>
      </div>

      {/* Đoạn kết luận: tối đa khoảng 5 dòng */}
      {conclusion && (
        <div
          style={{
            marginTop: 22,
            fontSize: 15.5,
            lineHeight: 1.7,
            color: '#334155',
            display: '-webkit-box',
            WebkitLineClamp: 5,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {conclusion}
        </div>
      )}

      {/* Phụ đề riêng của thẻ TA (9-Agent), đặt phía trên đường phân cách và chân thẻ */}
      <div style={{ marginTop: 22, fontSize: 12, color: '#94a3b8', lineHeight: 1.6 }}>
        AI 投研团队(9-Agent)深度分析
      </div>
    </ShareCardDialog>
  )
}
