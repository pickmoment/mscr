import { useEffect, useMemo, useRef, useState } from 'react';
import { api, InstrumentHit } from '../lib/api';

/**
 * 종목명·코드 검색 입력. 헤더의 전역 검색과 각 폼의 티커 칸이 같은 컴포넌트를 쓴다.
 * 값은 부모가 들고 있고(`value`/`onChange`), 항목을 고르면 `onPick`으로 같이 뜬 결과 목록까지 넘긴다 —
 * 그 목록이 종목 상세의 ◀▶ 이동 범위가 된다.
 */
export default function TickerSearch({ value, onChange, onPick, placeholder, ariaLabel, className }: {
  value: string;
  onChange: (next: string) => void;
  onPick: (hit: InstrumentHit, siblings: string[]) => void;
  placeholder?: string;
  ariaLabel: string;
  className?: string;
}) {
  const wrapper = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  // 응답 순서가 뒤바뀌어도 마지막 질의 결과만 반영한다.
  const sequence = useRef(0);
  const [hits, setHits] = useState<InstrumentHit[]>([]);
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  const [failed, setFailed] = useState(false);

  const needle = useMemo(() => value.trim(), [value]);
  useEffect(() => {
    if (!needle) { sequence.current += 1; setHits([]); setFailed(false); return; }
    const ticket = ++sequence.current;
    const timer = setTimeout(() => {
      api.searchInstruments(needle)
        .then(rows => { if (ticket !== sequence.current) return; setHits(rows); setFailed(false); setActive(0); })
        .catch(() => { if (ticket !== sequence.current) return; setHits([]); setFailed(true); });
    }, 200);
    return () => clearTimeout(timer);
  }, [needle]);

  useEffect(() => {
    const close = (event: MouseEvent) => { if (!wrapper.current?.contains(event.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);
  useEffect(() => { (list.current?.children[active] as HTMLElement | undefined)?.scrollIntoView({ block: 'nearest' }); }, [active, hits.length]);

  const pick = (hit: InstrumentHit) => { setOpen(false); onPick(hit, hits.map(item => item.ticker)); };
  const shown = open && !!needle;

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Escape') { setOpen(false); return; }
    if (!shown || !hits.length) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setActive(current => (current + (event.key === 'ArrowDown' ? 1 : hits.length - 1)) % hits.length);
    } else if (event.key === 'Enter' || event.key === 'Tab') {
      event.preventDefault();
      pick(hits[active]);
    }
  };

  return <div className="ticker-search" ref={wrapper}>
    <input
      className={className}
      value={value}
      spellCheck={false}
      placeholder={placeholder}
      aria-label={ariaLabel}
      aria-autocomplete="list"
      aria-expanded={shown && (hits.length > 0 || failed)}
      onChange={event => { onChange(event.target.value); setOpen(true); }}
      onFocus={() => setOpen(true)}
      onKeyDown={onKeyDown}
    />
    {shown && <ul className="ticker-search-list" role="listbox" ref={list}>
      {hits.map((hit, index) => <li
        key={hit.ticker}
        role="option"
        aria-selected={index === active}
        className={index === active ? 'active' : undefined}
        onMouseDown={event => { event.preventDefault(); pick(hit); }}
        onMouseEnter={() => setActive(index)}
      ><code>{hit.ticker}</code><span>{hit.name}</span><em>{hit.market ?? hit.kind.toUpperCase()}</em></li>)}
      {!hits.length && <li className="subtle">{failed ? '검색할 수 없습니다' : '일치하는 종목이 없습니다'}</li>}
    </ul>}
  </div>;
}
