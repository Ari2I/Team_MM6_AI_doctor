// Only synthetic text. No medical claims, customer requests, keys or personal data.
export const applicationId = '00000000-0000-0000-0000-000000000100'

export function savedReport() {
  const source = {
    number: 1, title: 'Синтетический источник', version: 'test-1', location: 'Тестовый раздел',
    quote: 'Дословная синтетическая цитата.\nВторая строка: TEST_EVIDENCE.',
    url: 'https://example.test/source', copy_url: null,
  }
  return {
    schema_version: 'ai-report.1', application_id: applicationId, saved_at: '2026-11-27T10:00:00Z',
    backend_revision: 'a'.repeat(40), ai_revision: 'b'.repeat(40), completeness: 'COMPLETE',
    human_report: {
      schema_version: 'human-report.1', title: 'Синтетический отчёт', notice: 'Решение принимает человек.',
      summary: 'Обработана одна синтетическая пара.', recommendation_note: 'Требуется сверка специалистом.',
      review_note: 'Финальная проверка обязательна.',
      items: [{
        number: 1, name: 'Синтетическая позиция', diagnosis: 'Синтетический диагноз',
        category: 'Б (предварительное предложение модели)', evidence: 'Основание требует сверки.',
        comment: 'Синтетическое пояснение.',
        established: [{ text: 'Входные сведения получены.', sources: [structuredClone(source)] }],
        uncertain: [{ text: 'Условие не подтверждено.', sources: [] }],
        questions: [{ text: 'Уточните условие.', why: 'Для сверки основания.', sources: [] }],
        actions: ['Передайте специалисту.'], sources: [source],
      }],
      costs: [{ amount: '1000.10', currency: 'RUB', currency_label: 'RUB', unknown_amount_count: 0 }],
      purchases_without_amount_count: 0,
    },
    checks: [{
      number: 1, item_id: '00000000-0000-0000-0000-000000000011',
      diagnosis_id: '00000000-0000-0000-0000-000000000001', row_number: 2,
      backend_rule: { category: null, source: 'Проверка backend отключена.', reference_version: null,
        comment: null, needs_review: true, review_reason: 'AI_REVIEW_REQUIRED' },
      ai_rule_category: null, ai_proposed_category: 'Б', classification_basis: 'ML2_PROPOSAL',
      conflict: false, processed: true, generation_mode: 'MODEL', form_status: 'UNKNOWN', reasons: ['NEEDS_REVIEW'],
    }],
  }
}
