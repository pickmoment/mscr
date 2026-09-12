import { IndicatorDefinition } from './api';
import type { Suggestion } from '../components/FormulaInput';

const signature = (item: IndicatorDefinition) => item.parameters.length ? `${item.key}(${item.parameters.map(parameter => parameter.name).join(', ')})` : item.formula || `${item.key}()`;
const keywords: Suggestion[] = [
  { key: 'and', label: '그리고', detail: '논리 연산자', call: false },
  { key: 'or', label: '또는', detail: '논리 연산자', call: false },
  { key: 'not', label: '부정', detail: '논리 연산자', call: false },
];

const detailOf = (item: IndicatorDefinition) => item.kind === 'function'
  ? (item.builtin ? item.formula || signature(item) : signature(item))
  : (item.series ? '시세 시리즈' : '스냅샷 값');

const entry = (item: IndicatorDefinition): Suggestion => ({ key: item.key, label: item.label, detail: detailOf(item), call: item.kind === 'function' });

export const screenSuggestions = (items: IndicatorDefinition[]): Suggestion[] => [
  ...items.filter(item => item.builtin || item.enabled).map(entry),
  ...keywords,
];

// 차트 수식이 볼 수 있는 이름은 시세 시리즈뿐이다(시가총액 같은 스냅샷 값은 봉마다 값이 없다).
export const chartSuggestions = (items: IndicatorDefinition[]): Suggestion[] =>
  items.filter(item => (item.builtin || item.enabled) && (item.kind === 'function' || item.series)).map(entry);

// 정의 수식은 스크린 수식과 같은 이름을 볼 수 있다. 다른 사용자 지표도 호출할 수 있고, 자기 자신만 순환 참조라 제외한다.
export const definitionSuggestions = (items: IndicatorDefinition[], parameters: string[], currentKey?: string): Suggestion[] => [
  ...parameters.filter(Boolean).map(name => ({ key: name, label: '파라미터', detail: '이 지표의 입력값', call: false })),
  ...items.filter(item => item.key !== currentKey && (item.builtin || item.enabled)).map(entry),
];
