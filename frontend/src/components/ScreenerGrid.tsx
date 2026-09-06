import { useMemo, useRef, useState } from 'react';
import { AgGridReact } from 'ag-grid-react';
import { AllCommunityModule, ModuleRegistry, themeQuartz, colorSchemeDark, colorSchemeLight, type ColDef, type ModelUpdatedEvent, type SelectionChangedEvent } from 'ag-grid-community';
import { api, ScreenRow } from '../lib/api';
import { compactVolume, won } from '../lib/format';
import { SelectTicker } from '../lib/nav';
import { readTokens, UI_FONT } from '../lib/tokens';
ModuleRegistry.registerModules([AllCommunityModule]);
export default function ScreenerGrid({ rows, sortFormula, onSelect, light }: { rows: ScreenRow[]; sortFormula: string; onSelect: SelectTicker; light: boolean }) {
  const [search, setSearch] = useState('');
  const [listName, setListName] = useState('');
  const [status, setStatus] = useState('');
  const [visibleCount, setVisibleCount] = useState(0);
  const gridRef = useRef<AgGridReact<ScreenRow>>(null);
  const columns = useMemo<ColDef<ScreenRow>[]>(() => [{ field: 'ticker', headerName: '티커', pinned: 'left', width: 90 }, { field: 'name', headerName: '종목명', pinned: 'left', width: 150, cellRenderer: (params: { value: string; data: ScreenRow }) => <span title={params.data.price_jump_flag ? '미수정주가 분할 의심 — 지표 신뢰도 낮음' : ''}>{params.value}{params.data.price_jump_flag ? ' ⚠' : ''}</span> }, { field: 'kind', headerName: '구분', width: 68 }, { field: 'market', headerName: '시장', width: 82 }, { field: 'close', headerName: '종가', type: 'numeric', filter: 'agNumberColumnFilter', valueFormatter: p => won(p.value) }, { field: 'change_pct', headerName: '등락률', type: 'numeric', valueFormatter: p => p.value == null ? '—' : `${p.value.toFixed(2)}%`, cellClassRules: { 'change-up': p => p.value > 0, 'change-down': p => p.value < 0 } }, { field: 'value', headerName: '거래대금', type: 'numeric', filter: 'agNumberColumnFilter', valueFormatter: p => compactVolume(p.value) }, { field: 'market_cap', headerName: '시총', type: 'numeric', valueFormatter: p => compactVolume(p.value) }, { field: 'weighted_return', headerName: '가중수익률', type: 'numeric', filter: 'agNumberColumnFilter', headerTooltip: '최근 3·6·9·12개월 누적수익률의 가중평균(0.4/0.2/0.2/0.2, 최근 분기 가중) — 12개월치 데이터가 없으면 빈 값', valueFormatter: p => p.value == null ? '—' : `${p.value.toFixed(1)}%`, cellClassRules: { 'change-up': p => p.value > 0, 'change-down': p => p.value < 0 } }, { field: '_sort', headerName: '정렬 수식값', type: 'numeric', headerTooltip: sortFormula ? `정렬 수식: ${sortFormula}` : '정렬 수식으로 계산한 값', valueFormatter: p => p.value == null ? '—' : Number(p.value).toLocaleString('ko-KR', { maximumFractionDigits: 4 }) }, { field: 'bars_available', headerName: '봉 수', type: 'numeric' }], [sortFormula]);
  const visibleTickers = () => {
    const tickers: string[] = [];
    gridRef.current?.api.forEachNodeAfterFilterAndSort(node => { if (node.data) tickers.push(node.data.ticker); });
    return tickers;
  };
  const onSelection = (event: SelectionChangedEvent<ScreenRow>) => { const row = event.api.getSelectedRows()[0]; if (row) onSelect(row.ticker, visibleTickers()); };
  const registerVisibleRows = async () => {
    const tickers = visibleTickers();
    try {
      const result = await api.createWatchlistFromTickers(listName.trim(), tickers);
      setListName('');
      setStatus(`'${result.name}' 목록에 ${result.added}종목을 담았습니다.${result.skipped.length ? ` (미등록 ${result.skipped.length}종목 제외)` : ''}`);
      window.dispatchEvent(new Event('mscr-watchlist-changed'));
    } catch (error) {
      setStatus(error instanceof Error ? error.message : '관심목록을 만들 수 없습니다.');
    }
  };
  // ag-grid는 CSS 변수를 못 읽어 색을 값으로 받아야 한다. 테마가 바뀌면 토큰을 다시 읽는다.
  const gridTheme = useMemo(() => {
    const t = readTokens();
    return themeQuartz.withPart(light ? colorSchemeLight : colorSchemeDark).withParams({ spacing: 4, rowHeight: 32, headerHeight: 34, fontFamily: UI_FONT, fontSize: 13, backgroundColor: t.surface, foregroundColor: t.text, borderColor: t.line, headerBackgroundColor: t.surface2, headerTextColor: t.text3, oddRowBackgroundColor: t.surface, rowHoverColor: t.surface3, selectedRowBackgroundColor: t.surface3, accentColor: t.accent, inputBackgroundColor: t.surface3, inputBorder: `1px solid ${t.line2}` });
  }, [light]);
  // 바깥 .screener-results 세로 플렉스가 이 조각들의 배치를 맡는다 — 여기서 다시 감싸면 판이 두 겹이 된다.
  return <>
    <div className="toolbar">
      <span className="subtle">{rows.length ? `${rows.length.toLocaleString('ko-KR')}종목 · 행을 클릭하면 종목 상세로 이동합니다` : ''}</span>
      <input className="w-lg" placeholder="종목명 / 티커 검색" value={search} onChange={e => setSearch(e.target.value)} />
      <input className="w-md push" placeholder="새 관심목록 이름" value={listName} maxLength={60} onChange={e => setListName(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && listName.trim() && visibleCount) registerVisibleRows(); }} />
      <button className="btn btn--primary" disabled={!listName.trim() || !visibleCount} onClick={registerVisibleRows} title="현재 표에 보이는 종목을 새 관심목록으로 담습니다">관심목록으로 담기 ({visibleCount})</button>
    </div>
    {status && <div className="msg">{status}</div>}
    {/* ag-grid 기본 빈 표 오버레이는 영어라 직접 안내한다. .empty는 grid라 자식마다 행이 생기므로 문장은 한 요소로 넘긴다. */}
    {rows.length === 0
      ? <div className="empty empty--inline"><span>아직 실행하지 않았거나 조건에 걸린 종목이 없습니다. 왼쪽에서 조건을 정하고 <b>스크린 실행</b>을 누르세요.</span></div>
      : <div className="grid-wrap"><AgGridReact ref={gridRef} theme={gridTheme} rowData={rows} columnDefs={columns} quickFilterText={search} rowSelection={{ mode: 'singleRow', checkboxes: false, enableClickSelection: true }} onSelectionChanged={onSelection} onModelUpdated={(event: ModelUpdatedEvent<ScreenRow>) => setVisibleCount(event.api.getDisplayedRowCount())} getRowId={({ data }) => data.ticker} columnTypes={{ numeric: { cellClass: 'ag-right-aligned-cell' } }} /></div>}
  </>;
}
