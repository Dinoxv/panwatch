import { useId } from 'react'

interface SparklineProps {
  /** Chuỗi số liệu (ví dụ giá đóng cửa / giá trị ròng 20 phiên gần nhất), tự chuẩn hóa min-max theo chiều cao khung vẽ */
  data: number[]
  width?: number
  height?: number
  /** Màu nét, nhận currentColor hoặc bất kỳ màu CSS nào (kể cả hsl(var(--xx))), đọc được ở cả giao diện sáng lẫn tối */
  stroke?: string
  /** Truyền vào thì dựng vùng tô chuyển sắc (trên nửa trong, dưới trong suốt); không truyền thì chỉ vẽ nét */
  fill?: string
  className?: string
}

/**
 * Đường diễn biến tối giản, không phụ thuộc thư viện biểu đồ bên thứ ba:
 * SVG polyline + vùng tô chuyển sắc tùy chọn + chấm tròn ở đuôi.
 * viewBox khớp chính xác width/height, kết hợp preserveAspectRatio="none" +
 * width="100%" để khung cha quyết định bề ngang dựng thật; độ dày nét dùng
 * vector-effect="non-scaling-stroke" nên không méo khi kéo ngang.
 */
export default function Sparkline({
  data,
  width = 100,
  height = 28,
  stroke = 'currentColor',
  fill,
  className,
}: SparklineProps) {
  const gradId = useId()
  const vals = (data || []).filter((v) => typeof v === 'number' && Number.isFinite(v))
  if (vals.length < 2) return null

  let min = Math.min(...vals)
  let max = Math.max(...vals)
  if (max - min < 1e-9) {
    const pad = Math.abs(max) * 0.02 || 1
    max += pad
    min -= pad
  }

  const n = vals.length
  const padY = Math.max(1.5, height * 0.12)
  const innerH = height - padY * 2
  const xAt = (i: number) => (width * i) / (n - 1)
  const yAt = (v: number) => padY + innerH - (innerH * (v - min)) / (max - min)
  const points = vals.map((v, i) => [xAt(i), yAt(v)] as const)
  const pointsAttr = points.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(' ')
  const [lastX, lastY] = points[n - 1]
  const fillColor = fill || stroke
  const gradientId = `spark-fill-${gradId.replace(/[^a-zA-Z0-9_-]/g, '')}`

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      width="100%"
      height={height}
      className={className}
      role="img"
      aria-hidden="true"
    >
      {fill && (
        <>
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={fillColor} stopOpacity={0.32} />
              <stop offset="100%" stopColor={fillColor} stopOpacity={0} />
            </linearGradient>
          </defs>
          <polygon
            points={`${xAt(0).toFixed(2)},${height} ${pointsAttr} ${xAt(n - 1).toFixed(2)},${height}`}
            fill={`url(#${gradientId})`}
            stroke="none"
          />
        </>
      )}
      <polyline
        points={pointsAttr}
        fill="none"
        stroke={stroke}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      <circle cx={lastX} cy={lastY} r={2.2} fill={stroke} />
    </svg>
  )
}
