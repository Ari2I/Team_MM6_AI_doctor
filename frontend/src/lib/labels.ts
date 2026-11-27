// Подписи, форматы чисел и вспомогательные функции для показа данных.
// Здесь нет запросов к серверу и нет расчётов результата проверки.
import type { CheckDetails, ItemDetails, Light } from '../contract'

// API не передаёт валюту, поэтому символ валюты не выводим.
export const money = new Intl.NumberFormat('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
export const percent = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 })

export const lights: Record<Light, string> = {
  green: 'Зелёный',
  yellow: 'Жёлтый',
  red: 'Красный',
  undetermined: 'Не определён — нужна проверка',
}

// Четыре «корзины» из category_shares. css — имя цвета в style.css.
export type CategoryKey = 'А' | 'Б' | 'В' | 'undetermined'
export const categoryKeys: CategoryKey[] = ['А', 'Б', 'В', 'undetermined']
export const categoryInfo: Record<CategoryKey, { label: string; hint: string; css: string }> = {
  'А': { label: 'Категория А', hint: 'Жизненно необходимые препараты', css: 'cat-a' },
  'Б': { label: 'Категория Б', hint: 'Сопутствующая терапия', css: 'cat-b' },
  'В': { label: 'Категория В', hint: 'Сомнительные или необязательные препараты', css: 'cat-c' },
  undetermined: { label: 'Не определена', hint: 'Категория не определена — нужна проверка специалистом', css: 'cat-u' },
}

// Названия столбцов Excel в сообщениях об ошибках.
export const columnNames: Record<string, string> = {
  drug_name: 'Препарат', mnn: 'МНН', amount: 'Сумма', diagnosis: 'Диагноз',
  diagnoses: 'Диагнозы', dosage: 'Дозировка', quantity: 'Количество', file: 'Файл',
}

// Если ИИ не смог завершить проверку, это нужно показать заметнее обычной отметки.
const aiFailureReasons = ['AI_UNAVAILABLE', 'AI_TIMEOUT', 'AI_INVALID_RESPONSE', 'AI_INPUT_INCOMPLETE']
export function isAiFailure(check: CheckDetails): boolean {
  return check.needs_review && aiFailureReasons.includes(check.review_reason ?? '')
}

export function itemNeedsReview(item: ItemDetails): boolean {
  return item.checks.some(check => check.needs_review)
}
export function itemHasNoCategory(item: ItemDetails): boolean {
  return item.checks.every(check => !check.category)
}

// Бэкенд кладёт цитату в конец комментария после слов « Цитата: ». Разделяем для показа.
export function splitComment(text: string | null | undefined): { main: string; quote: string } {
  if (!text) return { main: '', quote: '' }
  const marker = ' Цитата: '
  const index = text.indexOf(marker)
  return index < 0 ? { main: text, quote: '' } : { main: text.slice(0, index), quote: text.slice(index + marker.length) }
}
