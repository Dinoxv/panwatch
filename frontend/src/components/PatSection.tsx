import { useEffect, useState } from 'react'
import { Copy, Plus, Trash2, KeyRound } from 'lucide-react'
import { patsApi, type PatItem } from '@panwatch/api'
import { Input } from '@panwatch/base-ui/components/ui/input'
import { Button } from '@panwatch/base-ui/components/ui/button'
import { useToast } from '@panwatch/base-ui/components/ui/toast'

/**
 * Quản lý mã truy cập MCP (PAT).
 *
 * Mã dùng cho các MCP client như Claude kết nối tới endpoint MCP của PanWatch
 * (/mcp). Bản rõ chỉ trả về đúng một lần lúc tạo; danh sách chỉ hiện tiền tố.
 */
export default function PatSection() {
  const { toast } = useToast()
  const [items, setItems] = useState<PatItem[]>([])
  const [loading, setLoading] = useState(false)
  const [name, setName] = useState('')
  const [creating, setCreating] = useState(false)
  const [newToken, setNewToken] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const res = await patsApi.list()
      setItems(res.items || [])
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Tải mã truy cập thất bại', 'error')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const create = async () => {
    if (!name.trim()) {
      toast('Hãy điền ghi chú mục đích của mã', 'error')
      return
    }
    setCreating(true)
    try {
      const res = await patsApi.create({ name: name.trim() })
      setNewToken(res.token)
      setName('')
      await load()
      toast('Đã tạo mã. Bản rõ chỉ hiện lần này, hãy lưu lại ngay', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Tạo thất bại', 'error')
    } finally {
      setCreating(false)
    }
  }

  const revoke = async (id: number) => {
    try {
      await patsApi.revoke(id)
      await load()
      toast('Đã thu hồi mã', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Thu hồi thất bại', 'error')
    }
  }

  const copy = (text: string) => {
    navigator.clipboard?.writeText(text)
    toast('Đã sao chép vào bộ nhớ tạm', 'success')
  }

  return (
    <section id="sec-pat" className="card p-4 md:p-6 lg:col-span-12">
      <div className="flex items-start justify-between mb-4 gap-3">
        <div>
          <h3 className="text-[12px] md:text-[13px] font-semibold text-foreground flex items-center gap-1.5">
            <KeyRound className="w-3.5 h-3.5" /> Mã truy cập MCP
          </h3>
          <p className="text-[11px] text-muted-foreground mt-1">
            供 Claude 等 MCP 客户端连接本站 MCP 端点(<span className="font-mono">/mcp</span>), chỉ đọc dữ liệu giá và vị thế. Bản rõ chỉ hiện một lần lúc tạo.
          </p>
        </div>
      </div>

      {/* Tạo mới */}
      <div className="flex flex-col sm:flex-row gap-2 mb-4">
        <Input
          value={name}
          onChange={e => setName(e.target.value)}
          placeholder="Ghi chú mục đích của mã, ví dụ Claude Desktop"
          className="sm:max-w-xs"
        />
        <Button size="sm" className="h-9" onClick={create} disabled={creating}>
          <Plus className="w-3.5 h-3.5" /> Tạo mã
        </Button>
      </div>

      {/* Hiển thị bản rõ đúng một lần */}
      {newToken ? (
        <div className="mb-4 rounded-xl border border-amber-400/40 bg-amber-50/60 dark:bg-amber-950/20 p-3">
          <div className="text-[11px] text-amber-700 dark:text-amber-400 mb-1.5">
            请立即复制并妥善保存,关闭后无法再次查看:
          </div>
          <div className="flex items-center gap-2">
            <code className="flex-1 min-w-0 truncate rounded bg-background/70 px-2 py-1 font-mono text-[12px]">{newToken}</code>
            <Button variant="secondary" size="sm" className="h-8" onClick={() => copy(newToken)}>
              <Copy className="w-3.5 h-3.5" /> Sao chép
            </Button>
            <Button variant="ghost" size="sm" className="h-8" onClick={() => setNewToken(null)}>Đã hiểu</Button>
          </div>
        </div>
      ) : null}

      {/* Danh sách */}
      {loading ? (
        <div className="text-[12px] text-muted-foreground">Đang tải…</div>
      ) : items.length === 0 ? (
        <div className="text-[12px] text-muted-foreground">Chưa có mã nào.</div>
      ) : (
        <div className="space-y-2">
          {items.map(it => (
            <div
              key={it.id}
              className="flex items-center justify-between gap-3 rounded-xl border border-border/40 bg-accent/20 px-3 py-2"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-[12px] font-medium text-foreground truncate">{it.name || 'Chưa đặt tên'}</span>
                  <code className="font-mono text-[11px] text-muted-foreground">{it.prefix}…</code>
                  {it.revoked ? (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-rose-500/15 text-rose-600">Đã thu hồi</span>
                  ) : (
                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-500/15 text-emerald-600">Còn hiệu lực</span>
                  )}
                </div>
                <div className="text-[10px] text-muted-foreground mt-0.5">
                  {it.last_used_at ? `Dùng gần nhất ${it.last_used_at.slice(0, 10)}` : 'Chưa dùng lần nào'}
                  {it.expires_at ? ` · Hết hạn ${it.expires_at.slice(0, 10)}` : ' · Không hết hạn'}
                </div>
              </div>
              {!it.revoked ? (
                <Button variant="ghost" size="sm" className="h-8 text-rose-600" onClick={() => revoke(it.id)}>
                  <Trash2 className="w-3.5 h-3.5" /> Thu hồi
                </Button>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
