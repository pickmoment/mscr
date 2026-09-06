import { useMemo, useState } from 'react';
import type { ViewKey } from '../App';
import ViewHeader from './ViewHeader';
import Term from './Term';
import { glossary } from '../lib/glossary';

/**
 * 처음 여는 사람이 "무엇부터 눌러야 하는지" 알 수 있게 작업 순서를 앞 단계부터 세우고,
 * 각 단계에서 실제로 그 화면으로 넘어갈 수 있게 한다. 용어집은 `<Term/>` 팝오버와 같은 원본을 읽는다.
 */
type Step = { title: string; body: React.ReactNode; view: ViewKey; action: string };

const steps: Step[] = [
  {
    title: '일봉 수집',
    body: <>KRX에서 전 종목 일봉을 받아 로컬 SQLite에 넣습니다. 이게 없으면 아무 화면도 값을 못 냅니다.</>,
    view: 'settings', action: '설정 열기',
  },
  {
    title: '조건 만들기',
    body: <>스크리너에서 수식을 짜고 실행해 오늘 걸리는 종목을 봅니다. 마음에 들면 <Term id="preset" />으로 저장합니다.</>,
    view: 'screener', action: '스크리너 열기',
  },
  {
    title: '신호 로그 쌓기',
    body: <>저장한 프리셋을 과거 거래일마다 다시 돌려 <Term id="signal_log">기록</Term>을 남깁니다. 이게 있어야 신규·이탈과 검증이 가능합니다.</>,
    view: 'screener', action: '스크리너 열기',
  },
  {
    title: '프리셋 검증',
    body: <>신호 로그를 청산 규칙으로 재생해 <Term id="expectancy" />을 잽니다. 로그가 쌓인 기간만큼만 측정됩니다.</>,
    view: 'screener', action: '스크리너 열기',
  },
  {
    title: '관심종목',
    body: <>지켜볼 종목을 모으고 목표가를 달아 둡니다. 도달하면 브리핑에 올라옵니다.</>,
    view: 'watchlist', action: '관심종목 열기',
  },
  {
    title: '계획 세우기',
    body: <>진입가·손절가·분할 익절을 정합니다. 종목 상세 차트에서 선을 끌어 만들 수도 있습니다.</>,
    view: 'plans', action: '계획 열기',
  },
  {
    title: '주문 실행',
    body: <>조건을 충족한 계획만 브로커로 보냅니다. 먼저 <Term id="dry_run" />으로 무엇이 나갈지 확인하세요.</>,
    view: 'orders', action: '주문 열기',
  },
  {
    title: '복기',
    body: <>실제 체결로 계획별 성과를 <Term id="r">R</Term>로 결산하고 셋업별 성적을 봅니다.</>,
    view: 'review', action: '복기 열기',
  },
];

export default function HelpPanel({ onOpenView }: { onOpenView: (view: ViewKey) => void }) {
  const [query, setQuery] = useState('');
  // 이름만이 아니라 설명까지 걸러야 "R"로 기대값 같은 연관 항목이 함께 잡힌다.
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return glossary;
    return glossary.filter(entry => entry.term.toLowerCase().includes(needle) || entry.body.toLowerCase().includes(needle));
  }, [query]);

  return <div className="page">
    <div className="panel panel--pad stack stack--lg">
      <ViewHeader title="도움말" lede="이 툴의 작업 순서와 화면에 나오는 용어를 모았습니다." />

      <div>
        <div className="section-title">사용 흐름</div>
        <ol className="help-flow">
          {steps.map(step => <li key={step.title}>
            <b>{step.title}</b>{step.body}
            <div className="toolbar">
              <button className="btn btn--ghost btn--sm" onClick={() => onOpenView(step.view)}>{step.action}</button>
            </div>
          </li>)}
        </ol>
      </div>

      <div>
        <div className="toolbar">
          <div className="section-title">용어집 <span className="badge">{filtered.length}</span></div>
          <input
            className="w-lg push"
            placeholder="용어 검색"
            aria-label="용어 검색"
            value={query}
            onChange={event => setQuery(event.target.value)}
          />
        </div>
        {filtered.map(entry => <div className="help-term" key={entry.id}>
          <b>{entry.term}</b>
          <p>{entry.body}</p>
        </div>)}
        {!filtered.length && <div className="empty empty--inline">일치하는 용어가 없습니다.</div>}
      </div>
    </div>
  </div>;
}
