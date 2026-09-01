export const won = (value: number | null | undefined) => value == null ? '—' : `${Math.round(value).toLocaleString('ko-KR')}원`;
export const compactVolume = (value: number | null | undefined) => { if (value == null) return '—'; if (Math.abs(value) >= 1e8) return `${(value / 1e8).toFixed(1)}억`; if (Math.abs(value) >= 1e4) return `${(value / 1e4).toFixed(1)}만`; return Math.round(value).toLocaleString('ko-KR'); };
export const pct = (value: number | null | undefined) => value == null ? '—' : `${(value * 100).toFixed(2)}%`;
export const ratio = (value: number | null | undefined) => value == null ? '—' : value.toFixed(2);
