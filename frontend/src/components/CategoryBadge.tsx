import { categoryInfo } from '../lib/labels'
import type { CategoryKey } from '../lib/labels'

// Цветная плашка категории. category = null означает «не определена».
export function CategoryBadge({ category }: { category: CategoryKey | null | undefined }) {
  const key: CategoryKey = category ?? 'undetermined'
  const info = categoryInfo[key]
  return <span className={'badge ' + info.css} title={info.hint}>{info.label}</span>
}
