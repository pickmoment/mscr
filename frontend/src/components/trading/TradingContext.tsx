import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import { api, BrokerOrder, PlanEvaluation, RiskHeat, TradePlan, TradingStatus } from '../../lib/api';
import { Draft, emptyForm } from './shared';

/**
 * 계획·주문 실행·리스크 세 화면은 같은 계획 목록과 같은 브로커 상태를 본다.
 * 화면을 오갈 때마다 다시 불러오지 않도록 공유 상태와 로더를 이 Provider가 들고 있는다.
 */
type TradingValue = {
  status: TradingStatus | null; plans: TradePlan[]; evaluations: PlanEvaluation[];
  orders: BrokerOrder[]; heat: RiskHeat | null; setups: string[];
  armed: PlanEvaluation[]; evaluationOf: Map<number, PlanEvaluation>;
  message: string; busy: boolean;
  form: Draft; setForm: React.Dispatch<React.SetStateAction<Draft>>;
  setMessage: React.Dispatch<React.SetStateAction<string>>; setBusy: React.Dispatch<React.SetStateAction<boolean>>;
  // 한도 저장 응답이 새 히트를 그대로 주므로, 리스크 화면이 그 값을 다시 불러오지 않고 반영한다.
  setHeat: React.Dispatch<React.SetStateAction<RiskHeat | null>>;
  fail: (error: unknown, fallback: string) => void;
  loadPlans: () => void; loadOrders: () => void; loadHeat: () => void; loadStatus: () => void;
};

const TradingContext = createContext<TradingValue | null>(null);

export function useTrading() {
  const value = useContext(TradingContext);
  if (!value) throw new Error('useTrading()은 TradingProvider 안에서만 쓸 수 있습니다.');
  return value;
}

export function TradingProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<TradingStatus | null>(null);
  const [plans, setPlans] = useState<TradePlan[]>([]);
  const [evaluations, setEvaluations] = useState<PlanEvaluation[]>([]);
  const [orders, setOrders] = useState<BrokerOrder[]>([]);
  const [heat, setHeat] = useState<RiskHeat | null>(null);
  const [setups, setSetups] = useState<string[]>([]);
  const [form, setForm] = useState<Draft>(emptyForm);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const fail = (error: unknown, fallback: string) => setMessage(error instanceof Error ? error.message : fallback);
  const loadPlans = () => { api.tradePlans().then(setPlans).catch(error => fail(error, '계획 조회 실패')); api.evaluatePlans().then(setEvaluations).catch(() => setEvaluations([])); };
  const loadHeat = () => api.riskHeat().then(setHeat).catch(() => undefined);
  const loadOrders = () => api.tradeOrders().then(setOrders).catch(() => undefined);
  const loadStatus = () => api.tradingStatus().then(setStatus).catch(() => setStatus({ enabled: false, env: null, account_masked: null, source: null, reason: '브로커 상태를 확인할 수 없습니다.', active_env: 'paper', accounts: { paper: null, real: null } }));
  useEffect(() => { loadStatus(); loadPlans(); loadOrders(); loadHeat(); api.screens().then(rows => setSetups(rows.map(row => row.name))).catch(() => setSetups([])); }, []);
  useEffect(() => { window.addEventListener('mscr-settings-changed', loadStatus); return () => window.removeEventListener('mscr-settings-changed', loadStatus); }, []);
  // 종목 상세 차트에서 계획을 저장하면 이 화면들은 다시 마운트되지 않으므로 이벤트로 목록을 새로 읽는다.
  useEffect(() => { const reload = () => { loadPlans(); loadHeat(); }; window.addEventListener('mscr-plans-changed', reload); return () => window.removeEventListener('mscr-plans-changed', reload); }, []);
  const evaluationOf = useMemo(() => new Map(evaluations.map(item => [item.plan_id, item])), [evaluations]);
  const armed = useMemo(() => { const active = new Set(plans.filter(plan => plan.enabled).map(plan => plan.id)); return evaluations.filter(item => item.triggered && item.next_leg !== null && active.has(item.plan_id)); }, [plans, evaluations]);

  const value: TradingValue = {
    status, plans, evaluations, orders, heat, setups, armed, evaluationOf,
    message, busy, form, setForm, setMessage, setBusy, setHeat, fail,
    loadPlans, loadOrders, loadHeat, loadStatus,
  };
  return <TradingContext.Provider value={value}>{children}</TradingContext.Provider>;
}
