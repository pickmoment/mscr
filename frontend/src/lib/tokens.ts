// 차트·그리드는 CSS로 칠할 수 없어 색을 JS로 넘겨야 한다. 값은 theme.css의 토큰에서만 읽어
// 디자인 시스템과 어긋나지 않게 한다. data-theme이 바뀌면 다시 읽어야 하므로 함수로 둔다.
export type Tokens = {
  bg: string; surface: string; surface2: string; surface3: string;
  line: string; line2: string;
  text: string; text2: string; text3: string;
  accent: string; up: string; down: string; ok: string; warn: string; focus: string;
};

const NAMES: Record<keyof Tokens, string> = {
  bg: '--bg', surface: '--surface', surface2: '--surface-2', surface3: '--surface-3',
  line: '--line', line2: '--line-2',
  text: '--text', text2: '--text-2', text3: '--text-3',
  accent: '--accent', up: '--up', down: '--down', ok: '--ok', warn: '--warn', focus: '--focus',
};

export const readTokens = (): Tokens => {
  const style = getComputedStyle(document.documentElement);
  const read = (name: string) => style.getPropertyValue(name).trim();
  return Object.fromEntries(Object.entries(NAMES).map(([key, name]) => [key, read(name)])) as Tokens;
};

// 8자리 hex는 lightweight-charts·ag-grid 모두 받아준다. 토큰이 #rrggbb라는 전제 위에서만 쓴다.
export const alpha = (color: string, ratio: number) => `${color}${Math.round(Math.min(1, Math.max(0, ratio)) * 255).toString(16).padStart(2, '0')}`;

export const UI_FONT = "'Noto Sans KR', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
export const MONO_FONT = "'DM Mono', ui-monospace, SFMono-Regular, monospace";
