import { type PortfolioDiagnostics } from '@panwatch/api'
import ShareCardDialog from './ShareCardDialog'

interface DiagnosticsShareCardProps {
  open: boolean
  onClose: () => void
  diag: PortfolioDiagnostics
  /** Tùy chọn: lợi nhuận vượt thị trường chung trong N phiên gần nhất (%), có thì hiện ở nhóm chỉ tiêu phụ. */
  excessReturn?: number | null
  benchmarkLabel?: string
}

const UP = '#e11d48'
const DOWN = '#059669'
const NEUTRAL = '#d97706'
const SLATE = '#0f172a'

function signColor(v?: number | null): string {
  if (v == null || !isFinite(v)) return NEUTRAL
  if (v > 0) return UP
  if (v < 0) return DOWN
  return NEUTRAL
}

function pct(v?: number | null, digits = 1): string {
  if (v == null || !isFinite(v)) return '--'
  return `${v > 0 ? '+' : ''}${v.toFixed(digits)}%`
}

const MARKET_LABEL: Record<string, string> = { CN: 'Cổ phiếu A', HK: 'Cổ phiếu HK', US: 'Cổ phiếu Mỹ' }
const marketLabel = (m: string) => MARKET_LABEL[m] || m

/**
 * Diễn giải mức tập trung (HHI): 0~1, càng cao càng tập trung.
 * Từ 0,4 trở lên là cao; 0,25~0,4 là vừa phải; dưới 0,25 là phân tán.
 */
function hhiBand(hhi: number): { label: string; color: string } {
  if (hhi >= 0.4) return { label: 'Khá tập trung', color: NEUTRAL }
  if (hhi >= 0.25) return { label: 'Vừa phải', color: SLATE }
  return { label: 'Khá phân tán', color: DOWN }
}

function StatBox({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div
      style={{
        flex: 1,
        background: '#ffffff',
        border: '1px solid #e2e8f0',
        borderRadius: 12,
        padding: '12px 14px',
      }}
    >
      <div style={{ fontSize: 12, color: '#64748b', marginBottom: 6 }}>{label}</div>
      <div style={{ fontSize: 22, fontWeight: 800, color: color ?? SLATE, fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </div>
      {sub && <div style={{ fontSize: 11, color: '#94a3b8', marginTop: 2 }}>{sub}</div>}
    </div>
  )
}

/**
 * Thẻ khám sức khỏe danh mục. Đã che thông tin nhạy cảm: chỉ hiện tỷ trọng /
 * số lượng / cảnh báo rủi ro, tuyệt đối không hiện bất kỳ con số tiền nào.
 * total_market_value chỉ dùng để quy giá trị thị trường trong by_market thành
 * «tỷ trọng %», bản thân con số đó không được hiển thị.
 */
export default function DiagnosticsShareCard({
  open,
  onClose,
  diag,
  excessReturn,
  benchmarkLabel,
}: DiagnosticsShareCardProps) {
  const band = hhiBand(diag.hhi)
  const totalMv = diag.total_market_value || 0
  const markets = Object.entries(diag.by_market || {})
    .map(([m, v]) => ({ m, w: totalMv > 0 ? (v / totalMv) * 100 : 0 }))
    .sort((a, b) => b.w - a.w)
  const alerts = (diag.alerts || []).slice(0, 3)
  const hasExcess = excessReturn != null && isFinite(excessReturn)

  return (
    <ShareCardDialog open={open} onClose={onClose} filename="Thẻ khám sức khỏe danh mục">
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ fontSize: 22, fontWeight: 800, lineHeight: 1.2, color: SLATE }}>Khám danh mục</div>
        <div style={{ fontSize: 14, color: '#94a3b8', fontWeight: 500, flexShrink: 0 }}>Cấu trúc vị thế · rủi ro</div>
      </div>

      {/* Khối đầu: mức tập trung (HHI) */}
      <div
        style={{
          marginTop: 18,
          borderRadius: 18,
          padding: '22px 24px',
          background: `linear-gradient(135deg, ${band.color} 0%, ${band.color}cc 100%)`,
          color: '#ffffff',
          boxShadow: `0 10px 30px -8px ${band.color}66`,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: 1, opacity: 0.92, flexShrink: 0 }}>
            Mức tập trung (HHI)
          </div>
          <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
            <span style={{ fontSize: 42, fontWeight: 900, lineHeight: 1, fontVariantNumeric: 'tabular-nums' }}>
              {diag.hhi.toFixed(2)}
            </span>
            <span style={{ fontSize: 18, fontWeight: 700, marginLeft: 10 }}>{band.label}</span>
          </div>
        </div>
      </div>

      {/* Chỉ tiêu then chốt */}
      <div style={{ marginTop: 16, display: 'flex', gap: 12 }}>
        <StatBox label="Số vị thế" value={`${diag.position_count}`} sub="mã" />
        <StatBox
          label="Tỷ trọng vị thế lớn nhất"
          value={`${(diag.max_weight * 100).toFixed(0)}%`}
          color={diag.max_weight >= 0.4 ? NEUTRAL : SLATE}
        />
        {hasExcess && (
          <StatBox
            label={`Gần đây so với ${benchmarkLabel || 'Thị trường chung'}`}
            value={pct(excessReturn)}
            color={signColor(excessReturn)}
          />
        )}
      </div>

      {/* Phân bố thị trường */}
      {markets.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#334155', marginBottom: 8 }}>Phân bố thị trường</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {markets.map(({ m, w }) => (
              <div key={m}>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
                  <span style={{ color: '#475569' }}>{marketLabel(m)}</span>
                  <span style={{ color: SLATE, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
                    {w.toFixed(0)}%
                  </span>
                </div>
                <div style={{ height: 8, borderRadius: 999, background: '#e2e8f0', overflow: 'hidden' }}>
                  <div
                    style={{
                      height: '100%',
                      width: `${Math.min(100, w)}%`,
                      borderRadius: 999,
                      background: '#6366f1',
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Cảnh báo rủi ro */}
      <div style={{ marginTop: 16 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: '#334155', marginBottom: 8 }}>Cảnh báo rủi ro</div>
        {alerts.length > 0 ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {alerts.map((a, i) => (
              <div
                key={i}
                style={{
                  display: 'flex',
                  alignItems: 'flex-start',
                  gap: 8,
                  background: '#fffbeb',
                  border: '1px solid #fde68a',
                  borderRadius: 10,
                  padding: '10px 12px',
                  fontSize: 13,
                  lineHeight: 1.5,
                  color: '#92400e',
                }}
              >
                <span style={{ flexShrink: 0, fontWeight: 900 }}>!</span>
                <span>{a}</span>
              </div>
            ))}
          </div>
        ) : (
          <div
            style={{
              background: '#ecfdf5',
              border: '1px solid #a7f3d0',
              borderRadius: 10,
              padding: '10px 12px',
              fontSize: 13,
              color: '#065f46',
            }}
          >
            ✓ Mức tập trung / phân bố chưa thấy rủi ro rõ rệt
          </div>
        )}
      </div>
    </ShareCardDialog>
  )
}
