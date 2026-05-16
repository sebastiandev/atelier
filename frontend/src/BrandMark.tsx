import type { SVGProps } from "react";

type BrandMarkProps = SVGProps<SVGSVGElement> & {
  // Toggles the typing-cursor blink on the dash underneath the A.
  // Use on "live" surfaces (Home + WorkView topbar) where the
  // cursor metaphor lands; leave off on settings-shaped pages.
  blink?: boolean;
};

// Stencil A_ — replaces the old constellation tile. Inherits color
// from the surrounding text via currentColor so it works across the
// light, dark, and ANSI themes without per-theme overrides.
export function BrandMark({ blink = false, className, ...rest }: BrandMarkProps) {
  const classes = [
    "brand-mark",
    blink ? "brand-mark--blink" : "",
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <svg viewBox="0 0 64 64" className={classes} aria-hidden="true" {...rest}>
      <g
        fill="none"
        stroke="currentColor"
        strokeWidth="6"
        strokeLinecap="butt"
        strokeLinejoin="miter"
        strokeMiterlimit="10"
      >
        <path d="M 14 50 L 32 12 L 50 50" />
        <path d="M 21 36 L 43 36" />
      </g>
      <rect
        className="brand-mark__cursor"
        x="11"
        y="56"
        width="42"
        height="5"
        fill="currentColor"
      />
    </svg>
  );
}
