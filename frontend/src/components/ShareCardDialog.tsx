import { useRef, useState, type ReactNode } from 'react'
import { toPng } from 'html-to-image'
import { ImageDown, Loader2 } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'

interface ShareCardDialogProps {
  open: boolean
  onClose: () => void
  /** Tên tệp PNG xuất ra (không gồm phần mở rộng). */
  filename: string
  /** Bề ngang cố định của thẻ, mặc định 640. */
  width?: number
  /** Phần mặt trước của thẻ, do từng thẻ nghiệp vụ truyền vào. Màu phải nội tuyến tường minh, đừng dựa vào biến CSS của giao diện. */
  children: ReactNode
}

/**
 * Vỏ chung của thẻ chia sẻ: Dialog thống nhất + khung thẻ bề ngang cố định + chân
 * trang thương hiệu + nút «Tải ảnh».
 *
 * Điểm thiết kế:
 * - Khung thẻ bề ngang cố định (mặc định 640px), tự mang nền chuyển sắc trắng→#f8fafc,
 *   bo góc, đệm trong, phông hệ thống, chữ màu tối tường minh, để PNG xuất ra nhìn
 *   như nhau ở mọi giao diện (sáng/tối). Thẻ nghiệp vụ chỉ cần đưa phần «mặt trước»
 *   qua children.
 * - Chân trang (miễn trừ trách nhiệm + dòng dẫn PanWatch · github) do vỏ dựng thống
 *   nhất, làm mỏ neo nhất quán cho mọi thẻ chia sẻ.
 * - «Tải ảnh» dùng toPng của html-to-image (pixelRatio:2, cacheBust:true) xuất ra
 *   ${filename}.png.
 */
export default function ShareCardDialog({
  open,
  onClose,
  filename,
  width = 640,
  children,
}: ShareCardDialogProps) {
  const cardRef = useRef<HTMLDivElement>(null)
  const [busy, setBusy] = useState(false)

  const handleDownload = async () => {
    if (busy || !cardRef.current) return
    setBusy(true)
    try {
      const dataUrl = await toPng(cardRef.current, { pixelRatio: 2, cacheBust: true })
      const link = document.createElement('a')
      link.download = `${filename}.png`
      link.href = dataUrl
      link.click()
    } catch (e) {
      alert(e instanceof Error ? `Dựng ảnh thất bại: ${e.message}` : 'Dựng ảnh thất bại, xin thử lại')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Chia sẻ ảnh</DialogTitle>
          <DialogDescription>Xuất một tấm thẻ gọn gàng, chia sẻ được lên diễn đàn hay nhóm chat.</DialogDescription>
        </DialogHeader>

        {/* Vùng xem trước: lớp ngoài theo nền giao diện, thẻ bên trong tự mang màu tường minh */}
        <div className="flex justify-center overflow-x-auto rounded-xl bg-accent/30 p-4 scrollbar">
          {/* Thẻ xuất ra: bề ngang cố định, mọi màu nội tuyến tường minh, không dựa vào biến CSS của giao diện */}
          <div
            ref={cardRef}
            style={{
              width,
              boxSizing: 'border-box',
              background: 'linear-gradient(180deg, #ffffff 0%, #f8fafc 100%)',
              borderRadius: 24,
              padding: '32px 36px',
              border: '1px solid #e2e8f0',
              fontFamily:
                '-apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif',
              color: '#0f172a',
            }}
          >
            {/* Mặt trước thẻ nghiệp vụ */}
            {children}

            {/* Đường phân cách */}
            <div style={{ height: 1, background: '#e2e8f0', margin: '24px 0 16px' }} />

            {/* Chân trang: miễn trừ trách nhiệm + dòng dẫn thương hiệu (nhất quán ở mọi thẻ chia sẻ) */}
            <div style={{ fontSize: 12, color: '#94a3b8', lineHeight: 1.6 }}>
              Chỉ để tham khảo, không phải khuyến nghị đầu tư
            </div>
            <div
              style={{
                marginTop: 8,
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                fontSize: 13.5,
                fontWeight: 700,
                color: '#0f172a',
              }}
            >
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  width: 22,
                  height: 22,
                  borderRadius: 6,
                  background: '#0f172a',
                  color: '#ffffff',
                  fontSize: 13,
                  fontWeight: 900,
                  flexShrink: 0,
                }}
              >
                PW
              </span>
              <span>PanWatch</span>
              <span style={{ color: '#cbd5e1', fontWeight: 400 }}>·</span>
              <span style={{ color: '#64748b', fontWeight: 500, fontSize: 12.5 }}>
                github.com/TNT-Likely/PanWatch
              </span>
            </div>
          </div>
        </div>

        {/* Vùng thao tác */}
        <div className="mt-4 flex items-center justify-end gap-3">
          <Button variant="outline" size="sm" className="h-9" onClick={onClose} disabled={busy}>
            Đóng
          </Button>
          <Button size="sm" className="h-9" onClick={() => void handleDownload()} disabled={busy}>
            {busy ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <ImageDown className="w-3.5 h-3.5" />
            )}
            {busy ? 'Đang dựng…' : 'Tải ảnh'}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
