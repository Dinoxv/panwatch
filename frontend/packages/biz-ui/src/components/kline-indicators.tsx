import { HoverPopover } from '@panwatch/base-ui/components/ui/hover-popover'
import type { KlineSummaryData } from '@panwatch/biz-ui/components/kline-summary-dialog'
import { TechnicalBadge } from '@panwatch/biz-ui/components/technical-badge'

interface KlineIndicatorsProps {
  summary: KlineSummaryData
}

export function KlineIndicators({ summary: s }: KlineIndicatorsProps) {
  return (
    <div className="space-y-3">
      {/* Xu thế và hình mẫu (kèm giải thích) */}
      <div className="flex flex-wrap gap-2 text-[11px]">
        {s.trend && (
          <HoverPopover
            title="Xu thế (cách xếp đường trung bình)"
            content={
              <div className="space-y-2">
                <div>
                  <span className="font-medium text-foreground">Là gì:</span>
                  Nhãn xu thế lấy từ vị trí tương đối của các đường trung bình (MA5/MA10/MA20), tính trên giá đóng cửa nến ngày. MA càng ngắn càng nhạy, càng dài càng mượt.
                </div>
                <div>
                  <span className="font-medium text-foreground">Cách đọc thường gặp:</span>
                  <ul className="list-disc pl-4 mt-1 space-y-1">
                    <li><span className="font-medium text-foreground">Xếp tăng</span>(MA5 &gt; MA10 &gt; MA20): xu hướng đi lên «thuận» hơn, nhịp chỉnh thường nhìn hỗ trợ ở MA5/MA10 trước.</li>
                    <li><span className="font-medium text-foreground">Xếp giảm</span>(MA5 &lt; MA10 &lt; MA20): xu hướng đi xuống chiếm ưu thế, bật lại tới MA10/MA20 thường gặp kháng cự.</li>
                    <li><span className="font-medium text-foreground">Đường trung bình đan xen</span>: giai đoạn giằng co/sang tay, tín hiệu phụ thuộc nhiều hơn vào khối lượng và các mốc giá then chốt.</li>
                  </ul>
                </div>
              </div>
            }
            trigger={<TechnicalBadge label={s.trend} tone="neutral" help />}
          />
        )}

        {s.macd_status && (
          <HoverPopover
            title="MACD (xu thế/động lượng)"
            content={
              <div className="space-y-2">
                <div>
                  <span className="font-medium text-foreground">Là gì:</span>
                  MACD gồm hai đường (DIF/DEA) và thanh (hist). Khẩu độ thường gặp: DIF=EMA12-EMA26, DEA=EMA(DIF,9), hist≈(DIF-DEA)*2.
                </div>
                <div>
                  <span className="font-medium text-foreground">Nghĩa là gì:</span>
                  <ul className="list-disc pl-4 mt-1 space-y-1">
                    <li><span className="font-medium text-foreground">金叉</span>: DIF cắt lên DEA, động lượng ngắn hạn từ yếu chuyển mạnh.</li>
                    <li><span className="font-medium text-foreground">死叉</span>: DIF cắt xuống DEA, động lượng ngắn hạn từ mạnh chuyển yếu.</li>
                    <li><span className="font-medium text-foreground">Thanh dương/âm</span>: giá trị dương thường cho thấy động lượng bên mua trội hơn; giá trị âm thường cho thấy động lượng bên bán trội hơn.</li>
                  </ul>
                </div>
              </div>
            }
            trigger={<TechnicalBadge label={`MACD ${s.macd_status}`} tone="neutral" help />}
          />
        )}

        {s.rsi_status && (
          <HoverPopover
            title="RSI (sức mạnh tương đối)"
            content={
              <div className="space-y-2">
                <div>
                  <span className="font-medium text-foreground">Là gì:</span>
                  RSI đo sức mạnh tương đối giữa lực tăng và lực giảm trong một khoảng thời gian (0-100). Ở đây hiện RSI6 (6 phiên giao dịch gần nhất).
                </div>
                <div>
                  <span className="font-medium text-foreground">Ngưỡng tham khảo:</span>
                  <ul className="list-disc pl-4 mt-1 space-y-1">
                    <li>RSI6 &gt; 80: quá mua (rủi ro điều chỉnh cao hơn)</li>
                    <li>RSI6 70-80: thiên mạnh (động lượng nghiêng mua)</li>
                    <li>RSI6 &lt; 20: quá bán (xác suất bật lại tăng lên)</li>
                  </ul>
                </div>
              </div>
            }
            trigger={
              <TechnicalBadge
                label={`RSI ${s.rsi_status}${s.rsi6 != null ? ` (${s.rsi6.toFixed(0)})` : ''}`}
                tone={s.rsi_status === '超买' ? 'bullish' : s.rsi_status === '超卖' ? 'bearish' : 'neutral'}
                help
              />
            }
          />
        )}

        {s.kdj_status && (
          <HoverPopover
            title="KDJ (điểm đảo/quá mua quá bán)"
            content={<div>Giá trị J nhạy hơn, cắt lên/cắt xuống dùng để quan sát điểm đảo ngắn hạn, nhưng dễ bị nhiễu bởi giằng co, cần kết hợp xu thế và giá-khối lượng.</div>}
            trigger={<TechnicalBadge label={`KDJ ${s.kdj_status}`} tone="neutral" help />}
          />
        )}

        {s.volume_trend && (
          <HoverPopover
            title="Khối lượng (mức đồng thuận của khối lượng khớp lệnh)"
            content={<div>Khối lượng bùng lên thường dùng để xác nhận cú bứt phá hay nhịp bật lại có thật hay không; đẩy lên/rơi xuống mà khối lượng cạn thì dễ «ảo». Kết hợp với xu thế và mốc then chốt sẽ đáng tin hơn.</div>}
            trigger={
              <TechnicalBadge
                label={`${s.volume_trend}${s.volume_ratio != null ? ` (${s.volume_ratio.toFixed(1)}x)` : ''}`}
                tone={s.volume_trend === '放量' ? 'warning' : s.volume_trend === '缩量' ? 'info' : 'neutral'}
                help
              />
            }
          />
        )}

        {s.boll_status && (
          <HoverPopover
            title="Dải Bollinger (biến động/độ lệch)"
            content={<div>Vượt/thủng dải trên dải dưới hay gặp ở giai đoạn có xu thế hoặc lúc biến động cực đoan. Kết hợp khối lượng và cú kiểm tra lại/đứng vững để xác nhận.</div>}
            trigger={
              <TechnicalBadge
                label={`Bollinger ${s.boll_status}`}
                tone={s.boll_status === '突破上轨' ? 'bullish' : s.boll_status === '跌破下轨' ? 'bearish' : 'neutral'}
                help
              />
            }
          />
        )}

        {s.kline_pattern && (
          <HoverPopover
            title="Hình mẫu nến (cấu trúc cục bộ)"
            content={<div>Một cây nến đơn lẻ gợi ý được ít thôi, quan trọng hơn là vị trí nó đứng (trong xu thế/gần hỗ trợ kháng cự) và mức đồng thuận của khối lượng.</div>}
            trigger={<TechnicalBadge label={s.kline_pattern} tone="warning" help />}
          />
        )}
      </div>

      {/* Hỗ trợ kháng cự (kèm giải thích) */}
      <div className="flex flex-wrap gap-2 text-[11px]">
        {s.support != null && (
          <HoverPopover
            title="Vùng hỗ trợ (vùng hỗ trợ then chốt)"
            content={<div>Càng sát hỗ trợ càng dễ chặn đà giảm rồi bật lại; thủng xuống kèm khối lượng lớn thì hỗ trợ có thể đổi vai thành kháng cự. Nên xem là một «vùng» hơn là một điểm.</div>}
            trigger={<TechnicalBadge label={`Hỗ trợ ${s.support.toFixed(2)}`} tone="bearish" help />}
          />
        )}
        {s.resistance != null && (
          <HoverPopover
            title="Vùng kháng cự (vùng kháng cự then chốt)"
            content={<div>Càng sát kháng cự càng khó đi lên; bứt phá kèm khối lượng lớn rồi đứng vững thì kháng cự cũ thường đổi vai thành hỗ trợ.</div>}
            trigger={<TechnicalBadge label={`Kháng cự ${s.resistance.toFixed(2)}`} tone="bullish" help />}
          />
        )}
      </div>
    </div>
  )
}
