import { useEffect, useState } from 'react';
import { api, BriefData, Meta, SignalCoverage, TradingStatus } from '../lib/api';
import type { ViewKey } from '../App';

type Tone = 'danger' | 'warn' | 'accent' | 'ok';
type Step = { id: string; tone: Tone; title: string; body: string; action?: { label: string; view: ViewKey } };

const DAY_MS = 24 * 60 * 60 * 1000;
const STALE_DAYS = 5;

// 기준일이 며칠 전인지. 거래일이 아니라 달력일이라 주말·휴일도 함께 센다.
const daysSince = (iso: string): number | null => {
  const [year, month, day] = iso.slice(0, 10).split('-').map(Number);
  if (!year || !month || !day) return null;
  const now = new Date();
  return Math.floor((Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()) - Date.UTC(year, month - 1, day)) / DAY_MS);
};

const pct = (value: number | null | undefined) => value == null ? '—' : `${value.toFixed(2)}%`;

/**
 * 브리핑 맨 위의 "지금 막힌 단계" 카드. 이미 있는 조회만 읽어 다음에 눌러야 할 화면을 찍어 준다.
 * 조회 하나가 실패하면 그 조회를 쓰는 규칙만 건너뛴다 — 카드 전체를 내리면 진짜 막힌 단계까지 숨는다.
 */
export default function NextSteps({ data, onOpenView }: { data: BriefData | null; onOpenView: (view: ViewKey) => void }) {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [screens, setScreens] = useState<{ id: number }[] | null>(null);
  const [coverage, setCoverage] = useState<SignalCoverage | null>(null);
  const [trading, setTrading] = useState<TradingStatus | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let alive = true;
    const probe = <T,>(promise: Promise<T>, apply: (value: T) => void) =>
      promise.then(value => { if (alive) apply(value); }).catch(() => undefined);
    Promise.all([
      probe(api.meta(), setMeta),
      probe(api.screens(), rows => setScreens(rows.map(row => ({ id: row.id })))),
      probe(api.signalCoverage(), setCoverage),
      probe(api.tradingStatus(), setTrading),
    ]).then(() => { if (alive) setReady(true); });
    return () => { alive = false; };
  }, []);

  // 준비되기 전에 그리면 "막힌 단계가 없습니다"가 잠깐 스쳤다가 경고로 바뀐다.
  if (!ready) return null;

  const steps: Step[] = [];
  if (meta && meta.bars_rows === 0) steps.push({
    id: 'no-bars', tone: 'danger', title: '일봉 데이터가 없습니다',
    body: '아무 화면도 값을 낼 수 없습니다. 설정에서 수집을 한 번 돌리세요(최초 400일은 수십 분 걸립니다).',
    action: { label: '설정 열기', view: 'settings' },
  });
  if (meta && meta.bars_rows > 0 && meta.as_of) {
    const age = daysSince(meta.as_of);
    if (age != null && age > STALE_DAYS) steps.push({
      id: 'stale', tone: 'warn', title: `데이터가 ${age}일 전입니다`,
      body: `기준일 ${meta.as_of} 이후 일봉이 없습니다. 브리핑·스크리너 결과가 모두 그날 기준입니다.`,
      action: { label: '설정 열기', view: 'settings' },
    });
  }
  if (screens && screens.length === 0) steps.push({
    id: 'no-screens', tone: 'accent', title: '저장된 프리셋이 없습니다',
    body: '조건을 저장해야 신호 로그·검증·브리핑이 돌아갑니다.',
    action: { label: '스크리너 열기', view: 'screener' },
  });
  if (screens && screens.length > 0 && coverage && coverage.screens.every(row => row.days === 0)) steps.push({
    id: 'no-signals', tone: 'accent', title: '신호 로그가 비어 있습니다',
    body: '프리셋을 과거 거래일에 다시 돌려야 신규·이탈·연속 편입일이 나옵니다.',
    action: { label: '스크리너 열기', view: 'screener' },
  });
  if (data) {
    const unprotected = data.positions.filter(position => position.unprotected).length;
    if (unprotected) steps.push({
      id: 'unprotected', tone: 'danger', title: `손절 없는 보유 ${unprotected}종목`,
      body: '최대 손실이 정해지지 않아 리스크 합계에 잡히지 않습니다.',
      action: { label: '계획 만들기', view: 'plans' },
    });
    if (data.heat.over_limit) steps.push({
      id: 'heat', tone: 'danger', title: '히트 한도를 넘었습니다',
      body: `계획들이 모두 손절당하면 ${pct(data.heat.heat_pct)}를 잃습니다(한도 ${pct(data.heat.limits.max_portfolio_heat_pct)}).`,
      action: { label: '리스크 열기', view: 'risk' },
    });
    if (data.plans.length > 0 && trading && !trading.enabled) steps.push({
      id: 'no-broker', tone: 'warn', title: '브로커가 설정되지 않았습니다',
      body: '계획은 있지만 주문을 낼 수 없습니다. 모의 실행으로 판정만 볼 수 있습니다.',
      action: { label: '설정 열기', view: 'settings' },
    });
  }
  if (!steps.length) steps.push({
    id: 'clear', tone: 'ok', title: '막힌 단계가 없습니다',
    body: '데이터·프리셋·신호 로그·계획이 모두 준비된 상태입니다.',
  });

  return <div className="grid grid--auto next-steps">
    {steps.map(step => {
      const action = step.action;
      return <div className="stat-card" data-tone={step.tone} key={step.id}>
        <label>{step.title}</label>
        <span className="subtle">{step.body}</span>
        {action && <button className="btn btn--ghost btn--sm" onClick={() => onOpenView(action.view)}>{action.label}</button>}
      </div>;
    })}
  </div>;
}
