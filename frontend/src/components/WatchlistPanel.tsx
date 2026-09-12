import { useCallback, useEffect, useState } from 'react';
import { api, Watchlist, WatchlistDetail } from '../lib/api';
import { money } from '../lib/format';
import { SelectTicker } from '../lib/nav';
import TickerSearch from './TickerSearch';
import ViewHeader from './ViewHeader';

const changeClass = (value: number | null | undefined) => value == null ? '' : value > 0 ? 'change-up' : value < 0 ? 'change-down' : '';
const signedPct = (value: number | null | undefined) => value == null ? '—' : `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
const errorText = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback;

const emptyForm = { ticker: '', target_price: '', memo: '' };

export default function WatchlistPanel({ onSelect }: { onSelect: SelectTicker }) {
  const [lists, setLists] = useState<Watchlist[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<WatchlistDetail | null>(null);
  const [newName, setNewName] = useState('');
  const [renameValue, setRenameValue] = useState('');
  const [form, setForm] = useState(emptyForm);
  const [message, setMessage] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [targetId, setTargetId] = useState<number | null>(null);

  const loadLists = useCallback(async () => {
    const rows = await api.watchlists();
    setLists(rows);
    setSelectedId(current => (current && rows.some(row => row.id === current) ? current : rows[0]?.id ?? null));
    return rows;
  }, []);

  useEffect(() => { loadLists().catch(error => setMessage(errorText(error, '관심목록을 불러올 수 없습니다.'))); }, [loadLists]);
  useEffect(() => {
    const refresh = () => { loadLists().catch(() => undefined); };
    window.addEventListener('mscr-watchlist-changed', refresh);
    return () => window.removeEventListener('mscr-watchlist-changed', refresh);
  }, [loadLists]);
  useEffect(() => {
    if (selectedId == null) { setDetail(null); return; }
    setSelected([]);
    api.watchlistItems(selectedId).then(result => { setDetail(result); setRenameValue(result.name); }).catch(error => { setDetail(null); setMessage(errorText(error, '관심종목을 불러올 수 없습니다.')); });
  }, [selectedId, lists]);

  const run = async <T,>(action: () => Promise<T>, ok: string | ((result: T) => string), fallback: string) => {
    try {
      const result = await action();
      setMessage(typeof ok === 'function' ? ok(result) : ok);
      await loadLists();
      if (selectedId != null) setDetail(await api.watchlistItems(selectedId).catch(() => null));
      window.dispatchEvent(new Event('mscr-watchlist-changed'));
      return true;
    } catch (error) {
      setMessage(errorText(error, fallback));
      return false;
    }
  };

  const createList = async () => {
    if (!newName.trim()) return;
    try {
      const created = await api.createWatchlist(newName.trim());
      setNewName('');
      setMessage(`'${created.name}' 목록을 만들었습니다.`);
      await loadLists();
      setSelectedId(created.id);
      window.dispatchEvent(new Event('mscr-watchlist-changed'));
    } catch (error) {
      setMessage(errorText(error, '목록을 만들 수 없습니다.'));
    }
  };

  const addItem = async (event: React.FormEvent) => {
    event.preventDefault();
    const ticker = form.ticker.trim();
    if (!ticker) return;
    await run(() => api.saveWatchlistItem({ watchlist_id: selectedId, ticker, memo: form.memo || null, target_price: form.target_price ? Number(form.target_price) : null }), `${ticker}을(를) 담았습니다.`, '종목을 담을 수 없습니다.');
    setForm(emptyForm);
  };

  const patchItem = (ticker: string, memo: string | null, target: number | null) => run(() => api.saveWatchlistItem({ watchlist_id: selectedId, ticker, memo, target_price: target }), '저장했습니다.', '저장할 수 없습니다.');

  const otherLists = lists.filter(list => list.id !== selectedId);
  const targetList = otherLists.find(list => list.id === targetId) ?? otherLists[0] ?? null;
  const runBulk = async (action: 'delete' | 'move' | 'copy') => {
    if (selectedId == null || !selected.length) return;
    if (action !== 'delete' && !targetList) return;
    if (action === 'delete' && !window.confirm(`선택한 ${selected.length}종목을 '${detail?.name}' 목록에서 뺍니다. 계속할까요?`)) return;
    const done = await run(
      () => api.watchlistItemsAction(selectedId, { action, tickers: selected, target_id: action === 'delete' ? null : targetList!.id }),
      result => {
        const what = action === 'delete' ? '목록에서 뺐습니다' : action === 'move' ? `'${targetList!.name}'(으)로 옮겼습니다` : `'${targetList!.name}'에 복사했습니다`;
        return `${result.affected}종목을 ${what}.${result.skipped.length ? ` 대상 목록에 이미 있는 ${result.skipped.length}종목은 건너뛰었습니다.` : ''}`;
      },
      '선택 작업을 처리할 수 없습니다.');
    if (done) setSelected([]);
  };

  // 목록·표가 높이를 꽉 채우는 그리드라, 머리말은 그 바깥에서 한 행을 차지한다.
  return <div className="page page--fill">
    <ViewHeader
      title="관심종목"
      lede="감시할 종목을 목록 단위로 모으고 목표가·메모를 달아 둡니다. 목표가에 닿으면 브리핑에 올라옵니다."
    />
    <div className="watchlist-layout">
      <aside className="panel panel--pad scroll">
        <div className="stack stack--lg">
          <div>
            <div className="section-title">관심목록</div>
            <div className="list-nav">
              {lists.map(list => <button key={list.id} aria-current={list.id === selectedId} onClick={() => setSelectedId(list.id)}>
                <span>{list.name}</span><span className="badge">{list.item_count}</span>
              </button>)}
              {!lists.length && <div className="subtle">아직 관심목록이 없습니다. 아래에서 만들어 보세요.</div>}
            </div>
          </div>
          <div className="toolbar toolbar--tight">
            <input placeholder="새 목록 이름" value={newName} maxLength={60} onChange={event => setNewName(event.target.value)} onKeyDown={event => { if (event.key === 'Enter') createList(); }} />
            <button className="btn btn--primary" onClick={createList} disabled={!newName.trim()}>목록 추가</button>
          </div>
          {selectedId != null && <div className="stack">
            <div className="section-title">이름 변경</div>
            <div className="toolbar toolbar--tight">
              <input value={renameValue} maxLength={60} onChange={event => setRenameValue(event.target.value)} />
              <button className="btn btn--ghost" disabled={!renameValue.trim() || renameValue.trim() === detail?.name} onClick={() => run(() => api.renameWatchlist(selectedId, renameValue.trim()), '이름을 바꿨습니다.', '이름을 바꿀 수 없습니다.')}>변경</button>
            </div>
            <div className="toolbar">
              <button className="btn btn--danger" onClick={() => { if (window.confirm(`'${detail?.name}' 목록과 편입 종목을 모두 지웁니다. 계속할까요?`)) run(() => api.deleteWatchlist(selectedId), '목록을 지웠습니다.', '목록을 지울 수 없습니다.'); }}>목록 삭제</button>
            </div>
          </div>}
          {message && <div className="msg">{message}</div>}
        </div>
      </aside>
  
      {detail == null
        ? <div className="panel empty">관심목록을 선택하거나 새로 만드세요.</div>
        : <div className="watchlist-main">
          <div className="grid grid--4">
            {([['종목 수', `${detail.summary.count}종목`], ['평균 등락률', signedPct(detail.summary.avg_change_pct)], ['상승 / 하락', `${detail.summary.up} / ${detail.summary.down}`], ['목표가 도달', `${detail.summary.reached_target}종목`]] as const).map(([label, value]) => <div className="stat-card" key={label}>
              <label>{label}</label><strong className={label === '평균 등락률' ? changeClass(detail.summary.avg_change_pct) : ''}>{value}</strong>
            </div>)}
          </div>
          <div className="panel panel--tight scroll">
            <div className="toolbar">
              <div className="section-title">{detail.name} <span className="badge">{detail.rows.length}</span></div>
              <span className="badge">{detail.as_of || '시세 없음'} 기준</span>
              {detail.summary.stale && <span className="badge" data-tone="warn">일부 시세 없음</span>}
              <form className="toolbar toolbar--tight push" onSubmit={addItem}>
                <TickerSearch className="w-sm" placeholder="종목명 또는 코드" ariaLabel="관심종목 종목 검색"
                  value={form.ticker}
                  onChange={next => setForm(current => ({ ...current, ticker: next.toUpperCase() }))}
                  onPick={hit => setForm(current => ({ ...current, ticker: hit.ticker }))} />
                <input className="w-sm" placeholder="목표가" type="number" min="0" step="1" value={form.target_price} onChange={event => setForm(current => ({ ...current, target_price: event.target.value }))} />
                <input className="w-md" placeholder="메모" value={form.memo} maxLength={500} onChange={event => setForm(current => ({ ...current, memo: event.target.value }))} />
                <button className="btn btn--primary" type="submit" disabled={form.ticker.length !== 6}>담기</button>
              </form>
            </div>
            {!!selected.length && <div className="toolbar">
              <span className="badge" data-tone="accent">{selected.length}종목 선택</span>
              <select className="w-md" value={targetList?.id ?? ''} aria-label="대상 목록" disabled={!otherLists.length} onChange={event => setTargetId(Number(event.target.value))}>
                {otherLists.map(list => <option key={list.id} value={list.id}>{list.name}</option>)}
                {!otherLists.length && <option value="">다른 목록 없음</option>}
              </select>
              <button className="btn btn--ghost" disabled={!targetList} onClick={() => runBulk('move')} title="선택 종목을 대상 목록으로 옮깁니다">옮기기</button>
              <button className="btn btn--ghost" disabled={!targetList} onClick={() => runBulk('copy')} title="선택 종목을 대상 목록에도 담습니다">복사</button>
              <button className="btn btn--danger" onClick={() => runBulk('delete')}>선택 빼기</button>
              <button className="btn btn--ghost push" onClick={() => setSelected([])}>선택 해제</button>
            </div>}
            <table className="table table--nowrap">
              <thead><tr>
                <th><input type="checkbox" aria-label="전체 선택" checked={detail.rows.length > 0 && selected.length === detail.rows.length} onChange={event => setSelected(event.target.checked ? detail.rows.map(row => row.ticker) : [])} /></th>
                {['종목', '종가', '등락률', '편입가', '편입 후', '목표가', '목표까지', '메모', ''].map(label => <th key={label}>{label}</th>)}
              </tr></thead>
              <tbody>
                {detail.rows.map(row => <tr key={row.ticker} aria-selected={selected.includes(row.ticker)}>
                  <td><input type="checkbox" aria-label={`${row.name} 선택`} checked={selected.includes(row.ticker)} onChange={event => setSelected(current => event.target.checked ? [...current, row.ticker] : current.filter(item => item !== row.ticker))} /></td>
                  <td onClick={() => onSelect(row.ticker, detail.rows.map(item => item.ticker))} title="종목 상세로 이동">{row.name}<span className="subtle mono"> {row.ticker}</span>{row.halted ? ' ⏸' : ''}</td>
                  <td className="num">{money(row.close)}</td>
                  <td className={`num ${changeClass(row.change_pct)}`}>{signedPct(row.change_pct)}</td>
                  <td className="num">{money(row.added_price)}</td>
                  <td className={`num ${changeClass(row.since_added_pct)}`}>{signedPct(row.since_added_pct)}</td>
                  <td><input className="cell-input" type="number" min="0" step="1" defaultValue={row.target_price ?? ''} key={`target-${row.ticker}-${row.target_price ?? ''}`} aria-label={`${row.name} 목표가`}
                    onBlur={event => { const raw = event.target.value.trim(); const next = raw ? Number(raw) : null; if (next !== row.target_price) patchItem(row.ticker, row.memo, next); }} /></td>
                  <td className={`num ${row.target_gap_pct != null && row.target_gap_pct <= 0 ? 'change-up' : ''}`}>{signedPct(row.target_gap_pct)}</td>
                  <td><input className="cell-input" defaultValue={row.memo ?? ''} maxLength={500} key={`memo-${row.ticker}-${row.memo ?? ''}`} aria-label={`${row.name} 메모`}
                    onBlur={event => { const next = event.target.value.trim() || null; if (next !== row.memo) patchItem(row.ticker, next, row.target_price); }} /></td>
                  <td><button className="btn btn--ghost btn--sm" aria-label={`${row.name} 관심목록에서 빼기`} onClick={() => run(() => api.deleteWatchlistItem(detail.id, row.ticker), `${row.name}을(를) 뺐습니다.`, '삭제할 수 없습니다.')}>빼기</button></td>
                </tr>)}
                {!detail.rows.length && <tr><td colSpan={10} className="subtle">담은 종목이 없습니다. 종목코드를 입력해 담거나 종목 상세에서 ★ 버튼을 누르세요.</td></tr>}
              </tbody>
            </table>
          </div>
        </div>}
    </div>
  </div>;
}
