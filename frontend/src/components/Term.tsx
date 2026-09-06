import { useEffect, useRef, useState } from 'react';
import { entryOf } from '../lib/glossary';

/**
 * 용어 하나에 정의 팝오버를 붙인다. `<Term id="heat"/>`는 용어집의 이름을 그대로 쓰고,
 * `<Term id="heat">히트 비율</Term>`은 문장에 맞는 표기를 쓰되 같은 정의를 보여준다.
 * 등록되지 않은 id면 팝오버 없이 글자만 남긴다 — 오타가 화면을 깨지 않게.
 */
export default function Term({ id, children }: { id: string; children?: React.ReactNode }) {
  const entry = entryOf(id);
  const wrapper = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  // FormulaInput의 자동완성과 같은 방식 — 래퍼 밖을 누르면 닫는다.
  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => { if (!wrapper.current?.contains(event.target as Node)) setOpen(false); };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', escape);
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', escape); };
  }, [open]);

  if (!entry) return <>{children ?? id}</>;
  return <span className="term-wrap" ref={wrapper}>
    <button type="button" className="term" aria-expanded={open} onClick={() => setOpen(current => !current)}>{children ?? entry.term}</button>
    {open && <span className="term-pop" role="tooltip"><b>{entry.term}</b>{entry.body}</span>}
  </span>;
}
