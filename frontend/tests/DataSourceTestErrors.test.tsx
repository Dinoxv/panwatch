import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { TestErrorList } from '@/pages/DataSources'

describe('TestErrorList', () => {
  it('shows the symbol and market for entries that returned no data', () => {
    render(
      <TestErrorList
        errors={[{ symbol: 'APPL', market: 'US', error: 'Không có dữ liệu' }]}
      />,
    )

    expect(screen.getByText('APPL (US): Không có dữ liệu')).toBeTruthy()
  })
})
