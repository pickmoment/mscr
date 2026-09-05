import { useMemo, useRef, useState } from 'react';
import { AgGridReact } from 'ag-grid-react';
import { AllCommunityModule, ModuleRegistry, themeQuartz, colorSchemeDark, colorSchemeLight, type ColDef, type ModelUpdatedEvent, type SelectionChangedEvent } from 'ag-grid-community';
import { api, ScreenRow } from '../lib/api';
import { compactVolume, won } from '../lib/format';
import { SelectTicker } from '../lib/nav';
ModuleRegistry.registerModules([AllCommunityModule]);
const gridTypography = { spacing: 4, rowHeight: 32, headerHeight: 34, fontFamily: "'Noto Sans KR', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", fontSize: 13 };
const darkGridTheme = themeQuartz.withPart(colorSchemeDark).withParams(gridTypography);
const lightGridTheme = themeQuartz.withPart(colorSchemeLight).withParams(gridTypography);
export default function ScreenerGrid({ rows, onSelect, light }: { rows: ScreenRow[]; onSelect: SelectTicker; light: boolean }) {
  const [search, setSearch] = useState('');
  const [listName, setListName] = useState('');
  const [status, setStatus] = useState('');
  const [visibleCount, setVisibleCount] = useState(0);
  const gridRef = useRef<AgGridReact<ScreenRow>>(null);
  const columns = useMemo<ColDef<ScreenRow>[]>(() => [{ field: 'ticker', headerName: '티커', pinned: 'left', width: 90 }, { field: 'name', headerName: '종목명', pinned: 'left', width: 150, cellRenderer: (params: { value: string; data: ScreenRow }) => <span title={params.data.price_jump_flag ? '미수정주가 분할 의심 — 지표 신뢰도 낮음' : ''}>{params.value}{params.data.price_jump_flag ? ' ⚠' : ''}</span> }, { field: 'kind', headerName: '구분', width: 68 }, { field: 'market', headerName: '시장', width: 82 }, { field: 'close', headerName: '종가', type: 'numeric', filter: 'agNumberColumnFilter', valueFormatter: p => won(p.value) }, { field: 'change_pct', headerName: '등락률', type: 'numeric', valueFormatter: p => p.value == null ? '—' : `${p.value.toFixed(2)}%`, cellClassRules: { 'change-up': p => p.value > 0, 'change-down': p => p.value < 0 } }, { field: 'value', headerName: '거래대금', type: 'numeric', filter: 'agNumberColumnFilter', valueFormatter: p => compactVolume(p.value) }, { field: 'market_cap', headerName: '시총', type: 'numeric', valueFormatter: p => compactVolume(p.value) }, { field: 'weighted_return', headerName: '가중수익률', type: 'numeric', filter: 'agNumberColumnFilter', headerTooltip: '최근 3·6·9·12개월 누적수익률의 가중평균(0.4/0.2/0.2/0.2, 최근 분기 가중) — 12개월치 데이터가 없으면 빈 값', valueFormatter: p => p.value == null ? '—' : `${p.value.toFixed(1)}%`, cellClassRules: { 'change-up': p => p.value > 0, 'change-down': p => p.value < 0 } }, { field: '_sort', headerName: '정렬 수식값', type: 'numeric', valueFormatter: p => p.value == null ? '—' : Number(p.value).toLocaleString('ko-KR', { maximumFractionDigits: 4 }) }, { field: 'bars_available', headerName: '봉 수', type: 'numeric' }], []);
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
  return <section className="panel" style={{ padding: 16, minWidth: 0 }}>
    <div className="toolbar">
      <div className="section-title" style={{ margin: 0 }}>RESULTS <span className="badge">{rows.length}</span></div>
      <input placeholder="종목명 / 티커 검색" value={search} onChange={e => setSearch(e.target.value)} />
      <input placeholder="새 관심목록 이름" value={listName} maxLength={60} style={{ marginLeft: 'auto' }} onChange={e => setListName(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && listName.trim() && visibleCount) registerVisibleRows(); }} />
      <button className="primary" disabled={!listName.trim() || !visibleCount} onClick={registerVisibleRows} title="현재 표에 보이는 종목을 새 관심목록으로 담습니다">관심목록으로 담기 ({visibleCount})</button>
    </div>
    {status && <div className="notice" style={{ marginBottom: 10 }}>{status}</div>}
    <div className="grid-wrap"><AgGridReact ref={gridRef} theme={light ? lightGridTheme : darkGridTheme} rowData={rows} columnDefs={columns} quickFilterText={search} rowSelection={{ mode: 'singleRow', checkboxes: false, enableClickSelection: true }} onSelectionChanged={onSelection} onModelUpdated={(event: ModelUpdatedEvent<ScreenRow>) => setVisibleCount(event.api.getDisplayedRowCount())} getRowId={({ data }) => data.ticker} columnTypes={{ numeric: { cellClass: 'ag-right-aligned-cell' } }} /></div>
  </section>;
}
