import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { TraceTimeline } from '@/components/assistant/TraceTimeline'

describe('TraceTimeline', () => {
  it('renders factual runtime events without reasoning content', () => {
    render(
      <TraceTimeline
        events={[
          { event: 'run_started', data: { task_id: 7 } },
          { event: 'context_prepared', data: { compressed: true } },
          { event: 'tool_call_start', data: { name: 'get_portfolio', arguments: { market: 'CN' } } },
          { event: 'tool_result', data: { name: 'get_portfolio', ok: true, preview: 'Truy vấn danh mục xong' } },
          { event: 'model_usage', data: { input_tokens: 120, output_tokens: 30 } },
          { event: 'done', data: {} },
        ]}
      />,
    )

    expect(screen.getByTestId('assistant-trace')).toBeTruthy()
    expect(screen.getByText(/Đã xong/)).toBeTruthy()
    expect(screen.queryByText('Ngữ cảnh đã nén và sẵn sàng')).toBeNull()
    expect(screen.queryByText('Gọi công cụ: get_portfolio')).toBeNull()
    expect(screen.queryByText(/思考过程|chain of thought/i)).toBeNull()
  })

  it('shows provider token usage as a factual runtime event', async () => {
    const user = userEvent.setup()
    render(
      <TraceTimeline
        events={[
          { event: 'model_usage', data: { input_tokens: 120, output_tokens: 30 } },
          { event: 'done', data: {} },
        ]}
      />,
    )

    await user.click(screen.getByRole('button', { name: /Nhật ký thực thi/ }))
    expect(screen.getByText('Mức dùng mô hình: vào 120, ra 30')).toBeTruthy()
  })

  it('expands the factual steps from the compact summary', async () => {
    const user = userEvent.setup()
    render(
      <TraceTimeline
        events={[
          { event: 'tool_call_start', data: { name: 'get_portfolio', arguments: { market: 'CN' } } },
          { event: 'tool_result', data: { name: 'get_portfolio', ok: true, preview: 'Truy vấn danh mục xong' } },
          {
            event: 'extension_event',
            data: {
              extension: 'tool_research',
              event: 'completed',
              data: { selected_tools: ['get_portfolio'] },
            },
          },
          { event: 'done', data: {} },
        ]}
      />,
    )

    await user.click(screen.getByRole('button', { name: /Nhật ký thực thi/ }))

    expect(screen.getByText('Gọi công cụ: get_portfolio')).toBeTruthy()
    expect(screen.getByText('{"market":"CN"}')).toBeTruthy()
    expect(screen.getByText('Truy vấn danh mục xong')).toBeTruthy()
    expect(screen.getByText('Rà công cụ xong: chọn được 1')).toBeTruthy()
  })

  it('distinguishes tool exposure and model-side search from execution', async () => {
    const user = userEvent.setup()
    render(
      <TraceTimeline
        events={[
          {
            event: 'extension_event',
            data: {
              extension: 'tool_research',
              event: 'exposure',
              data: { direct_tools: ['get_quote'], loaded_tools: [] },
            },
          },
          {
            event: 'extension_event',
            data: {
              extension: 'tool_research',
              event: 'searched',
              data: { selected_tools: ['get_fundamentals'] },
            },
          },
          { event: 'tool_call_start', data: { name: 'get_fundamentals', arguments: {} } },
          { event: 'done', data: {} },
        ]}
      />,
    )

    await user.click(screen.getByRole('button', { name: /Nhật ký thực thi/ }))

    expect(screen.getByText('Danh mục công cụ đã sẵn sàng: 1 công cụ dùng thẳng, 0 công cụ đã nạp')).toBeTruthy()
    expect(screen.getByText('Tìm công cụ xong: nạp 1')).toBeTruthy()
    expect(screen.getByText('Gọi công cụ: get_fundamentals')).toBeTruthy()
  })
})
