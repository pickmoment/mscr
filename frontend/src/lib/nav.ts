/**
 * 종목 선택 콜백. `siblings`는 클릭한 화면에 함께 보이던 종목 목록을 표시 순서 그대로 넘긴 것으로,
 * 종목 상세의 앞뒤 이동 범위가 된다.
 */
export type SelectTicker = (ticker: string, siblings: string[]) => void;
