import type { DeepAnalysisResult } from '@panwatch/api'

export interface AnalysisSection {
  id: string
  title: string
  markdown: string
}

/**
 * Ghép các phần từ raw_data của phân tích chuyên sâu (nội dung quyết định / bốn
 * chuyên viên phân tích / tranh luận xem tăng xem giảm / tranh luận kiểm soát rủi ro).
 * Tab của hộp thoại và bài dài của trang đọc chi tiết dùng chung logic ghép này,
 * để hai chỗ khỏi dựng lệch nhau.
 * Thứ tự ở đây chính là thứ tự trên xuống của trang chi tiết, trái sang phải của
 * tab hộp thoại. Chỉ trả về những phần có nội dung.
 */
export function buildAnalysisSections(
  rawData: Partial<DeepAnalysisResult['raw_data']>,
): AnalysisSection[] {
  const reports = rawData.analyst_reports || { market: '', social: '', news: '', fundamentals: '' }
  const debate = rawData.debate_history
  const riskDebate = rawData.risk_debate
  const sections: AnalysisSection[] = []

  // Bản quyết định: tiêu đề section dùng thẳng «Bản quyết định cuối của PM» (bỏ tiêu đề «Quyết định cuối» lặp lại trước đó);
  // Kế hoạch thực thi của trader giữ làm tiêu đề con (để phân biệt với bản quyết định).
  const decisionBody = [
    rawData.final_decision || '',
    rawData.trader_plan && `### 💼 Kế hoạch thực thi của trader\n\n${rawData.trader_plan}`,
  ]
    .filter(Boolean)
    .join('\n\n')
  if (decisionBody) sections.push({ id: 'decision', title: 'Bản quyết định cuối của PM', markdown: decisionBody })

  // Bốn chuyên viên phân tích
  const analysts: [string, string][] = [
    ['market', 'Chuyên viên phân tích kỹ thuật'],
    ['social', 'Chuyên viên phân tích tâm lý'],
    ['news', 'Chuyên viên phân tích tin tức'],
    ['fundamentals', 'Chuyên viên phân tích cơ bản'],
  ]
  for (const [k, title] of analysts) {
    const text = (reports as unknown as Record<string, string>)[k] || ''
    if (text) sections.push({ id: k, title, markdown: text })
  }

  // Tranh luận xem tăng xem giảm (đội nghiên cứu: lịch sử tranh luận + phán quyết của trưởng nhóm nghiên cứu)
  if (debate?.history) {
    let dc = debate.history
    if (debate.judge_decision) dc += `\n\n### ⚖️ Phán quyết của trưởng nhóm nghiên cứu\n\n${debate.judge_decision}`
    sections.push({ id: 'debate', title: 'Tranh luận xem tăng xem giảm', markdown: dc })
  }

  // Tranh luận kiểm soát rủi ro (đội kiểm soát rủi ro: quyết liệt/trung lập/thận trọng tranh luận + phán quyết kiểm soát rủi ro)
  if (riskDebate?.history) {
    let rc = riskDebate.history
    if (riskDebate.judge_decision) rc += `\n\n### 🛡️ Phán quyết kiểm soát rủi ro\n\n${riskDebate.judge_decision}`
    sections.push({ id: 'risk', title: 'Tranh luận kiểm soát rủi ro', markdown: rc })
  }

  return sections
}
