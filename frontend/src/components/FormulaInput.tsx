import { useEffect, useMemo, useRef, useState } from 'react';

export type Suggestion = { key: string; label: string; detail: string; call: boolean };

type Token = { text: string; start: number };

const tokenAt = (text: string, caret: number): Token | null => {
  const match = /[A-Za-z_][A-Za-z0-9_]*$/.exec(text.slice(0, caret));
  return match ? { text: match[0], start: caret - match[0].length } : null;
};

const rank = (suggestion: Suggestion, needle: string) => {
  const key = suggestion.key.toLowerCase();
  if (key.startsWith(needle)) return 0;
  if (key.includes(needle)) return 1;
  return 2;
};

export default function FormulaInput({ value, onChange, suggestions, multiline = false, placeholder, ariaLabel, required }: { value: string; onChange: (next: string) => void; suggestions: Suggestion[]; multiline?: boolean; placeholder?: string; ariaLabel?: string; required?: boolean }) {
  const wrapper = useRef<HTMLDivElement>(null);
  const ref = useRef<HTMLTextAreaElement & HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const [token, setToken] = useState<Token | null>(null);
  const [active, setActive] = useState(0);
  useEffect(() => {
    const close = (event: MouseEvent) => { if (!wrapper.current?.contains(event.target as Node)) setToken(null); };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, []);

  const matches = useMemo(() => {
    if (!token) return [];
    const needle = token.text.toLowerCase();
    // 목록은 max-height + overflow 로 스크롤된다. 입력한 토큰으로 이미 좁혀지므로 임의로 자르지 않는다.
    return suggestions
      .filter(item => rank(item, needle) < 2 && item.key.toLowerCase() !== needle)
      .sort((left, right) => rank(left, needle) - rank(right, needle) || left.key.localeCompare(right.key));
  }, [token, suggestions]);
  useEffect(() => { (list.current?.children[active] as HTMLElement | undefined)?.scrollIntoView({ block: 'nearest' }); }, [active, matches.length]);

  const syncToken = (element: HTMLTextAreaElement | HTMLInputElement) => {
    setToken(tokenAt(element.value, element.selectionStart ?? element.value.length));
    setActive(0);
  };

  const apply = (suggestion: Suggestion) => {
    if (!token) return;
    const insert = suggestion.call ? `${suggestion.key}(` : suggestion.key;
    const caret = token.start + insert.length;
    onChange(`${value.slice(0, token.start)}${insert}${value.slice(token.start + token.text.length)}`);
    setToken(null);
    requestAnimationFrame(() => { const element = ref.current; if (element) { element.focus(); element.setSelectionRange(caret, caret); } });
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement | HTMLInputElement>) => {
    if (!matches.length) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setActive(current => (current + (event.key === 'ArrowDown' ? 1 : matches.length - 1)) % matches.length);
    } else if (event.key === 'Enter' || event.key === 'Tab') {
      event.preventDefault();
      apply(matches[active]);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      setToken(null);
    }
  };

  const shared = {
    ref,
    value,
    placeholder,
    required,
    spellCheck: false,
    'aria-label': ariaLabel,
    'aria-autocomplete': 'list' as const,
    'aria-expanded': matches.length > 0,
    onChange: (event: React.ChangeEvent<HTMLTextAreaElement | HTMLInputElement>) => { onChange(event.target.value); syncToken(event.target); },
    onKeyUp: (event: React.KeyboardEvent<HTMLTextAreaElement | HTMLInputElement>) => { if (!['ArrowDown', 'ArrowUp', 'Enter', 'Tab', 'Escape'].includes(event.key)) syncToken(event.currentTarget); },
    onClick: (event: React.MouseEvent<HTMLTextAreaElement | HTMLInputElement>) => syncToken(event.currentTarget),
    onKeyDown,
  };

  return <div className="formula-input" ref={wrapper}>
    {multiline ? <textarea {...shared} /> : <input className="control" {...shared} />}
    {matches.length > 0 && <ul className="formula-suggest" role="listbox" aria-label="지표 자동완성" ref={list}>
      {matches.map((item, index) => <li key={item.key} role="option" aria-selected={index === active} className={index === active ? 'active' : undefined} onMouseDown={event => { event.preventDefault(); apply(item); }} onClick={() => apply(item)} onMouseEnter={() => setActive(index)}>
        <code>{item.key}</code><span>{item.label}</span><em>{item.detail}</em>
      </li>)}
    </ul>}
  </div>;
}
