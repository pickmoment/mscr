import { useEffect, useState } from 'react';
import { ScreenRow } from '../lib/api';
import { SelectTicker } from '../lib/nav';
import ViewHeader from './ViewHeader';
import Term from './Term';
import ScreenerPanel from './ScreenerPanel';
import ScreenerGrid from './ScreenerGrid';
import SignalLogPanel from './SignalLogPanel';
import PresetVerifyPanel from './PresetVerifyPanel';

type Pane = 'results' | 'signals' | 'verify';

export default function ScreenerView({ onSelect, light }: { onSelect: SelectTicker; light: boolean }) {
  const [rows, setRows] = useState<ScreenRow[]>([]);
  const [preset, setPreset] = useState<{ id: number; name: string } | null>(null);
  const [sortFormula, setSortFormula] = useState('');
  const [pane, setPane] = useState<Pane>('results');
  // 프리셋을 놓으면 뒤 두 탭이 잠기므로, 잠긴 탭에 머물러 빈 화면을 보여주지 않는다.
  useEffect(() => { if (!preset) setPane('results'); }, [preset]);

  const tabs: { key: Pane; label: string }[] = [
    { key: 'results', label: `결과 (${rows.length})` },
    { key: 'signals', label: '신호 로그' },
    { key: 'verify', label: '프리셋 검증' },
  ];

  // .split은 height:100%라 머리말과 나란히 두면 화면 밖으로 넘친다. 바깥을 두 줄 격자로 잡아 남은 높이만 준다.
  return <div className="page page--fill"><ViewHeader
    title="스크리너"
    lede={<>전 종목에 조건 수식을 돌려 오늘 걸리는 종목을 찾습니다. 조건은 왼쪽에서 정하고, 저장한 <Term id="preset" />은 신호 로그·검증·브리핑에서 다시 쓰입니다.</>}
  />
  <div className="split">
    <ScreenerPanel onResults={setRows} onPresetChange={setPreset} onSortFormulaChange={setSortFormula} />
    <section className="panel screener-results">
      <nav className="subtabs subtabs--inline" role="tablist" aria-label="스크리너 결과 보기">
        {tabs.map(tab => <button
          key={tab.key}
          className="subtab"
          type="button"
          role="tab"
          aria-selected={pane === tab.key}
          disabled={tab.key !== 'results' && !preset}
          title={tab.key !== 'results' && !preset ? '먼저 왼쪽에서 프리셋을 선택하세요' : undefined}
          onClick={() => setPane(tab.key)}
        >{tab.label}</button>)}
      </nav>
      {pane === 'results' && <ScreenerGrid rows={rows} sortFormula={sortFormula} onSelect={onSelect} light={light} />}
      {pane === 'signals' && preset && <SignalLogPanel screenId={preset.id} screenName={preset.name} onSelect={onSelect} />}
      {pane === 'verify' && preset && <PresetVerifyPanel screenId={preset.id} screenName={preset.name} />}
    </section>
  </div></div>;
}
