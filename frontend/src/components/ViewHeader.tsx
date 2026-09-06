/**
 * 모든 화면의 머리말. 제목 한 줄과 "이 화면이 무엇을 읽어 무엇을 내는지" 한 문단을 고정 자리에 둔다.
 * meta는 제목 옆의 배지(기준일 등), actions는 우측으로 밀리는 버튼 묶음이다.
 */
export default function ViewHeader({ title, lede, meta, actions }: {
  title: string; lede: React.ReactNode; meta?: React.ReactNode; actions?: React.ReactNode;
}) {
  return <header className="view-head">
    <div className="view-head-row"><h1>{title}</h1>{meta}{actions && <div className="toolbar push">{actions}</div>}</div>
    <p className="view-lede">{lede}</p>
  </header>;
}
