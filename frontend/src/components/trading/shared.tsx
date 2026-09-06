/** 계획·주문·리스크·복기 네 화면이 함께 쓰는 폼 타입·라벨·표시 포맷·표 머리글. */

export type Draft = { id: number | null; name: string; ticker: string; side: 'buy' | 'sell'; quantity: string; order_type: 'limit' | 'market'; limit_price: string; entry_price: string; stop_price: string; tp1_price: string; tp1_ratio: string; tp2_price: string; tp2_ratio: string; tp3_trailing_pct: string; enabled: boolean; setup: string; note: string };
export const emptyForm: Draft = { id: null, name: '', ticker: '', side: 'buy', quantity: '', order_type: 'limit', limit_price: '', entry_price: '', stop_price: '', tp1_price: '', tp1_ratio: '', tp2_price: '', tp2_ratio: '', tp3_trailing_pct: '', enabled: true, setup: '', note: '' };
export const statusLabel: Record<string, string> = { dry_run: '모의', submitted: '접수', partial: '부분체결', filled: '체결', rejected: '거부', failed: '실패', skipped: '건너뜀' };
export const statusTone: Record<string, string | undefined> = { filled: 'ok', rejected: 'danger', failed: 'danger', submitted: 'live', partial: 'live', dry_run: undefined, skipped: undefined };
export const legLabel: Record<string, string> = { entry: '진입', stop: '손절 청산', tp1: '1차 익절', tp2: '2차 익절', trailing: '트레일링 청산' };
// 계획 기간 시뮬레이션의 기본 구간 — 오늘, 그리고 그 180일 전.
export const isoDaysAgo = (days: number) => { const date = new Date(); date.setDate(date.getDate() - days); return date.toISOString().slice(0, 10); };
export const num = (value: number | null | undefined) => value == null ? '—' : value.toLocaleString('ko-KR', { maximumFractionDigits: 4 });
export const pct = (value: number | null | undefined) => value == null ? '—' : `${(value * 100).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}%`;
// 리스크·복기 응답의 *_pct는 이미 0~100 퍼센트라 그대로 찍는다. 위의 pct()는 0~1 비율용이라 섞으면 100배가 어긋난다.
export const pctPoint = (value: number | null | undefined, digits = 2) => value == null ? '—' : `${value.toFixed(digits)}%`;
export const rMultiple = (value: number | null | undefined) => value == null ? '—' : `${value.toFixed(2)}R`;
export const stateLabel: Record<string, string> = { pending: '대기', open: '진행', closed: '종료', disabled: '중지' };
export const stateTone: Record<string, string | undefined> = { pending: undefined, open: 'live', closed: 'ok', disabled: undefined };
export const rTone = (value: number | null | undefined) => value == null ? 'subtle' : value >= 0 ? 'up' : 'down';
export const bindingLabel: Record<string, string> = { max_loss: '최대 손실 금액이 수량을 결정', max_investment: '최대 투자 금액이 수량을 결정' };
// 수동 폼은 모든 값을 문자열로 들고 있다. 표시용 포맷과 상태용 문자열은 분리하고, 상태에는 자릿수를 줄이지 않은 값을 넣는다.
export const fieldText = (value: number | null | undefined) => value == null ? '' : String(value);
export type ProposeDraft = { ticker: string; side: 'buy' | 'sell'; entry_price: string; max_investment: string; max_loss: string };
export const emptyPropose: ProposeDraft = { ticker: '', side: 'buy', entry_price: '', max_investment: '', max_loss: '' };
export const candidateColumns: { label: string; title?: string; num?: boolean }[] = [
  { label: '선택' },
  { label: '손절폭', title: '손절가까지의 거리를 ATR의 몇 배로 잡는지입니다.' },
  { label: '손절가', num: true },
  { label: '수량', num: true },
  { label: '실투자액', num: true },
  { label: '최대손실액', num: true, title: '손절가에 그대로 체결됐을 때의 손실입니다. 갭 하락으로 손절가를 건너뛰면 보장되지 않습니다.' },
  { label: '한도소진', num: true, title: '입력한 최대 손실 금액 중 이 후보가 쓰는 비율입니다.' },
  { label: '분할', num: true, title: '1차 익절 / 2차 익절 / 트레일링 레그에 배분되는 주식 수입니다.' },
  { label: '1차 목표가', num: true },
  { label: '2차 목표가', num: true },
  { label: '1차 도달확률', num: true, title: '표본 기간 동안 손절보다 1차 목표가에 먼저 도달한 비율입니다.' },
  { label: '2차 도달확률', num: true },
  { label: '본전 필요 1차', num: true, title: '이 구조가 본전이 되려면 1차 목표 도달률이 최소 이 값 이상이어야 합니다. 같은 행의 1차 도달확률과 비교하세요.' },
  { label: '기준선 기대R', num: true, title: '진입 근거가 없을 때의 과거 기준선입니다. 예측이 아니며, 진입 판단이 이 기준선을 넘어야 이익이 납니다.' },
  { label: '트레일링', num: true },
];

export type LimitDraft = { risk: string; heat: string };
export const riskColumns: { label: string; title?: string; num?: boolean }[] = [
  { label: '이름' },
  { label: '종목' },
  { label: '상태', title: '대기 = 아직 진입 전, 진행 = 체결되어 보유 중, 종료 = 청산 완료, 중지 = 사용 꺼짐입니다.' },
  { label: '수량', num: true },
  { label: '현재 위험', num: true, title: '지금 손절가에 닿으면 잃는 금액입니다. 진입 후 손절가를 올렸다면 최초 위험보다 작아집니다.' },
  { label: '최초 위험', num: true, title: '계획을 세울 때의 진입가와 손절가로 계산한 손실 금액입니다.' },
  { label: '자산 대비', num: true, title: '현재 위험이 총자산에서 차지하는 비율입니다.' },
];

export const reviewColumns: { label: string; title?: string; num?: boolean }[] = [
  { label: '진입일' },
  { label: '종목' },
  { label: '이름' },
  { label: '셋업' },
  { label: '상태' },
  { label: '진입가', num: true, title: '실제 체결가와 계획가 대비 슬리피지입니다. 양수는 불리하게 체결됐다는 뜻입니다.' },
  { label: '실현 R', num: true, title: '청산된 물량의 손익을 1R(=진입가-손절가)로 나눈 값입니다.' },
  { label: '미실현 R', num: true, title: '아직 들고 있는 물량의 평가손익을 R로 환산한 값입니다.' },
  { label: 'MAE R', num: true, title: '보유 기간 중 가장 불리했던 지점까지의 폭입니다.' },
  { label: 'MFE R', num: true, title: '보유 기간 중 가장 유리했던 지점까지의 폭입니다.' },
  { label: '보유일', num: true },
];

/** 누적 R 곡선. 차트 라이브러리를 붙일 만한 밀도가 아니라 인라인 SVG로 그린다. */
export function EquitySpark({ points }: { points: { date: string; cumulative_r: number }[] }) {
  const width = 260, height = 48, pad = 4;
  const values = points.map(point => point.cumulative_r);
  const top = Math.max(...values, 0), bottom = Math.min(...values, 0);
  const span = top - bottom || 1;
  const path = points.map((point, index) => {
    const x = pad + index * (width - pad * 2) / (points.length - 1);
    const y = pad + (top - point.cumulative_r) * (height - pad * 2) / span;
    return `${index === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`;
  }).join(' ');
  const zeroY = pad + top * (height - pad * 2) / span;
  const last = points[points.length - 1];
  return <div className="toolbar toolbar--tight">
    <span className="subtle mono">{points[0].date} {rMultiple(points[0].cumulative_r)}</span>
    <span className={rTone(last.cumulative_r)}>
      <svg width={width} height={height} role="img" aria-label="누적 R 곡선">
        <line x1={pad} x2={width - pad} y1={zeroY} y2={zeroY} stroke="currentColor" strokeOpacity="0.3" strokeDasharray="3 3" />
        <path d={path} fill="none" stroke="currentColor" strokeWidth="1.5" />
      </svg>
    </span>
    <span className={`mono ${rTone(last.cumulative_r)}`}>{last.date} {rMultiple(last.cumulative_r)}</span>
  </div>;
}
