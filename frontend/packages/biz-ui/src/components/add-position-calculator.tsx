import { useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { insightApi, type AddPositionEvalResult } from '@panwatch/api'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

export interface AddPositionCalc {
  newQty: number
  newCost: number
  diluteAbs: number
  dilutePct: number
  totalInvested: number
  isAdd: boolean
}

/** Giá vốn bình quân sau khi gia tăng tỷ trọng (tính xuôi). Nhập không hợp lệ thì trả null. */
export function calcAddPosition(
  curQty: number,
  curCost: number,
  addQty: number,
  addPrice: number,
): AddPositionCalc | null {
  if (!(addQty > 0) || !(addPrice > 0)) return null
  const newQty = curQty + addQty
  if (!(newQty > 0)) return null
  const newCost = (curQty * curCost + addQty * addPrice) / newQty
  const isAdd = curQty > 0 && curCost > 0
  const diluteAbs = isAdd ? curCost - newCost : 0
  const dilutePct = isAdd && curCost > 0 ? (diluteAbs / curCost) * 100 : 0
  return { newQty, newCost, diluteAbs, dilutePct, totalInvested: newQty * newCost, isAdd }
}

/** Tính ngược: muốn hạ giá vốn xuống target thì phải mua thêm bao nhiêu cổ ở giá addPrice. Chỉ khả thi khi addPrice < target < curCost. */
export function calcSharesForTargetCost(
  curQty: number,
  curCost: number,
  addPrice: number,
  target: number,
): number | null {
  if (!(curQty > 0) || !(curCost > 0)) return null
  if (!(addPrice > 0) || !(target > 0)) return null
  if (!(addPrice < target && target < curCost)) return null
  const q = (curQty * (curCost - target)) / (target - addPrice)
  return q > 0 ? q : null
}

function fmt(n: number | null | undefined, d = 2): string {
  if (n == null || !isFinite(n)) return '--'
  return n.toFixed(d)
}
function fmtInt(n: number | null | undefined): string {
  if (n == null || !isFinite(n)) return '--'
  return Math.round(n).toLocaleString()
}

// Khóa của hai bảng dưới đây là kết luận thô do backend trả về (trường verdict,
// vẫn là tiếng Trung) — đừng dịch khóa, dịch là tra không ra. Chữ hiển thị nằm ở
// VERDICT_LABEL.
const VERDICT_STYLE: Record<string, string> = {
  适合: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
  谨慎: 'bg-amber-500/15 text-amber-600 border-amber-500/30',
  不适合: 'bg-rose-500/15 text-rose-500 border-rose-500/30',
  未知: 'bg-muted text-muted-foreground border-border',
}

const VERDICT_LABEL: Record<string, string> = {
  适合: 'Hợp',
  谨慎: 'Thận trọng',
  不适合: 'Không hợp',
  未知: 'Không rõ',
}

interface Props {
  symbol: string
  market: string
  currentQuantity: number
  currentCost: number
  currentPrice?: number | null
}

export default function AddPositionCalculator({
  symbol,
  market,
  currentQuantity,
  currentCost,
  currentPrice,
}: Props) {
  const { toast } = useToast()
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<'shares' | 'amount'>('shares')
  const [addRaw, setAddRaw] = useState('')
  const [priceRaw, setPriceRaw] = useState('')
  const [targetRaw, setTargetRaw] = useState('')
  const [aiLoading, setAiLoading] = useState(false)
  const [aiResult, setAiResult] = useState<AddPositionEvalResult | null>(null)

  const isCN = market === 'CN'

  const addPrice = useMemo(() => {
    const p = parseFloat(priceRaw)
    if (isFinite(p) && p > 0) return p
    return currentPrice && currentPrice > 0 ? currentPrice : 0
  }, [priceRaw, currentPrice])

  // Nhập (số cổ/số tiền) → số cổ mua thêm
  const addQty = useMemo(() => {
    const v = parseFloat(addRaw)
    if (!isFinite(v) || v <= 0) return 0
    if (mode === 'shares') return v
    return addPrice > 0 ? v / addPrice : 0
  }, [addRaw, mode, addPrice])

  const calc = useMemo(
    () => calcAddPosition(currentQuantity, currentCost, addQty, addPrice),
    [currentQuantity, currentCost, addQty, addPrice],
  )

  const reverseShares = useMemo(() => {
    const t = parseFloat(targetRaw)
    if (!isFinite(t) || t <= 0) return null
    return calcSharesForTargetCost(currentQuantity, currentCost, addPrice, t)
  }, [targetRaw, currentQuantity, currentCost, addPrice])

  const runAi = async () => {
    if (!calc || addQty <= 0 || addPrice <= 0) {
      toast('Xin điền số cổ/số tiền mua thêm và giá hợp lệ trước', 'error')
      return
    }
    setAiLoading(true)
    setAiResult(null)
    try {
      const res = await insightApi.addPositionEval({
        symbol,
        market,
        current_quantity: currentQuantity,
        current_cost: currentCost,
        add_quantity: Math.round(addQty),
        add_price: addPrice,
      })
      setAiResult(res)
    } catch (e: any) {
      toast(e?.message || 'Đánh giá AI thất bại', 'error')
    } finally {
      setAiLoading(false)
    }
  }

  const pricePlaceholder = currentPrice && currentPrice > 0 ? String(currentPrice) : 'Giá mua thêm'
  const hasHolding = currentQuantity > 0 && currentCost > 0
  const lotWarn = isCN && addQty > 0 && Math.round(addQty) % 100 !== 0

  return (
    <div className="mt-3 border-t border-border/50 pt-3">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between text-[11px] text-muted-foreground"
      >
        <span>Tính mua thêm{hasHolding ? '' : '(hiện đang trống danh mục · tính cho lần mở vị thế)'}</span>
        <span>{open ? 'Thu lại ▾' : 'Mở ra ▸'}</span>
      </button>

      {open && (
        <div className="mt-2 space-y-2 text-[12px]">
          <div className="flex gap-1">
            {(['shares', 'amount'] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={`rounded border px-2 py-0.5 text-[11px] ${
                  mode === m
                    ? 'border-primary bg-primary text-primary-foreground'
                    : 'border-border text-muted-foreground'
                }`}
              >
                {m === 'shares' ? 'Theo số cổ' : 'Theo số tiền'}
              </button>
            ))}
          </div>

          <div className="grid grid-cols-2 gap-2">
            <label className="space-y-1">
              <div className="text-[10px] text-muted-foreground">
                {mode === 'shares' ? 'Số cổ mua thêm' : 'Số tiền mua thêm (đồng)'}
              </div>
              <Input
                value={addRaw}
                onChange={(e) => setAddRaw(e.target.value)}
                inputMode="decimal"
                placeholder={mode === 'shares' ? 'Ví dụ 200' : 'Ví dụ 10000'}
              />
            </label>
            <label className="space-y-1">
              <div className="text-[10px] text-muted-foreground">Giá mua thêm</div>
              <Input
                value={priceRaw}
                onChange={(e) => setPriceRaw(e.target.value)}
                inputMode="decimal"
                placeholder={pricePlaceholder}
              />
            </label>
          </div>

          {mode === 'amount' && addQty > 0 && (
            <div className="text-[10px] text-muted-foreground">
              ≈ {fmtInt(addQty)} cổ{isCN ? `(≈${fmtInt(addQty / 100)} lô)` : ''}
            </div>
          )}

          {calc ? (
            <div className="space-y-1 rounded bg-accent/15 p-2">
              <div className="flex justify-between">
                <span className="text-muted-foreground">{calc.isAdd ? 'Giá vốn sau khi mua thêm' : 'Giá vốn khi mở vị thế'}</span>
                <span className="font-mono">{fmt(calc.newCost)}</span>
              </div>
              {calc.isAdd && (
                <div className="flex justify-between">
                  <span className="text-muted-foreground">Bình quân</span>
                  <span className={`font-mono ${calc.diluteAbs >= 0 ? 'text-emerald-500' : 'text-rose-500'}`}>
                    {calc.diluteAbs >= 0 ? '↓' : '↑'}
                    {fmt(Math.abs(calc.diluteAbs))} ({fmt(Math.abs(calc.dilutePct))}%)
                  </span>
                </div>
              )}
              <div className="flex justify-between">
                <span className="text-muted-foreground">Tổng số cổ / vốn bỏ vào</span>
                <span className="font-mono">
                  {fmtInt(calc.newQty)} / {fmtInt(calc.totalInvested)}
                </span>
              </div>
              {lotWarn && (
                <div className="text-[10px] text-amber-600">Mẹo: cổ phiếu A thường 100 cổ/lô, nên làm tròn về bội số của 100</div>
              )}
            </div>
          ) : (
            <div className="text-[11px] text-muted-foreground">Điền số cổ/số tiền mua thêm và giá là tự tính</div>
          )}

          {hasHolding && (
            <div className="grid grid-cols-2 items-end gap-2">
              <label className="space-y-1">
                <div className="text-[10px] text-muted-foreground">Tính ngược: giá vốn mục tiêu</div>
                <Input
                  value={targetRaw}
                  onChange={(e) => setTargetRaw(e.target.value)}
                  inputMode="decimal"
                  placeholder={`< ${fmt(currentCost)}`}
                />
              </label>
              <div className="pb-1 text-[11px]">
                {targetRaw.trim() === '' ? (
                  <span className="text-muted-foreground">Tính ngược số cổ cần mua theo giá mua thêm</span>
                ) : reverseShares != null ? (
                  <span>
                    Cần thêm <span className="font-mono text-foreground">{fmtInt(reverseShares)}</span> cổ
                    {isCN ? `(≈${fmtInt(Math.ceil(reverseShares / 100))} lô)` : ''}
                    <br />khoảng <span className="font-mono">{fmtInt(reverseShares * addPrice)}</span> đồng
                  </span>
                ) : (
                  <span className="text-amber-600">Phải có giá mua thêm &lt; mục tiêu &lt; giá vốn hiện tại thì mới hạ về mức đó được</span>
                )}
              </div>
            </div>
          )}

          <div className="pt-1">
            <Button
              size="sm"
              variant="secondary"
              className="w-full"
              disabled={aiLoading || !calc}
              onClick={runAi}
            >
              {aiLoading ? 'AI đang đánh giá…' : 'Để AI đánh giá có nên mua thêm không'}
            </Button>
          </div>

          {aiResult && (
            <div className="space-y-1 rounded border border-border/60 p-2">
              <div className="flex items-center gap-2">
                <span
                  className={`rounded border px-2 py-0.5 text-[11px] ${
                    VERDICT_STYLE[aiResult.verdict] || VERDICT_STYLE['Không rõ']
                  }`}
                >
                  {VERDICT_LABEL[aiResult.verdict] || aiResult.verdict}
                </span>
                <span className="text-[10px] text-muted-foreground">Kết luận AI · chỉ để tham khảo</span>
              </div>
              <div className="prose prose-sm dark:prose-invert max-w-none break-words text-[12px] leading-relaxed [&_p]:my-1 [&_ul]:my-1">
                <ReactMarkdown>{aiResult.content}</ReactMarkdown>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
