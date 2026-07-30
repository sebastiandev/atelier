import type { HTMLAttributes, ReactNode } from "react";

type TileHeaderProps = HTMLAttributes<HTMLElement> & {
  left: ReactNode;
  meta?: ReactNode;
  right?: ReactNode;
};

export function TileHeader({
  left,
  meta,
  right,
  className,
  ...headerProps
}: TileHeaderProps) {
  return (
    <header {...headerProps} className={className || undefined}>
      <div className="tile-header-left">{left}</div>
      <div className="tile-header-meta">{meta}</div>
      <div className="tile-header-right">{right}</div>
    </header>
  );
}
