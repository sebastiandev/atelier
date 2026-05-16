import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & {
  size?: number;
};

// v3 settings icon — three rows of "sliders" with offset knobs. Reads
// cleanly at 11–16px and stays distinct from the sun (light-theme
// toggle), unlike the more common gear-with-rays which is visually
// near-identical to a sun at small sizes.
export function SlidersIcon({ size = 16, ...rest }: IconProps) {
  return (
    <svg
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      aria-hidden
      {...rest}
    >
      <path d="M2.5 4h7M11.5 4h2M2.5 8h2M6.5 8h7M2.5 12h7M11.5 12h2" />
      <circle cx="10.5" cy="4" r="1.3" fill="currentColor" stroke="none" />
      <circle cx="5.5" cy="8" r="1.3" fill="currentColor" stroke="none" />
      <circle cx="10.5" cy="12" r="1.3" fill="currentColor" stroke="none" />
    </svg>
  );
}

// Magnifier — used by the rail's Search action in v3.
export function SearchIcon({ size = 16, ...rest }: IconProps) {
  return (
    <svg
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      aria-hidden
      {...rest}
    >
      <circle cx="7" cy="7" r="4.5" />
      <path d="M10.4 10.4l3 3" />
    </svg>
  );
}

// Plug — connections nav item.
export function PlugIcon({ size = 14, ...rest }: IconProps) {
  return (
    <svg
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      {...rest}
    >
      <path d="M6 3v3M10 3v3M5 6h6v3a3 3 0 0 1-6 0V6zM8 12v2" />
    </svg>
  );
}

// Tiny chevron for breadcrumb-style "next" indicators.
export function ChevronRightIcon({ size = 12, ...rest }: IconProps) {
  return (
    <svg
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      {...rest}
    >
      <path d="M6 3l5 5-5 5" />
    </svg>
  );
}

// Small check used in "Verified" rows (no pill background, just the
// check + the word "Verified" in muted text).
export function CheckIcon({ size = 12, ...rest }: IconProps) {
  return (
    <svg
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      {...rest}
    >
      <path d="M3 8.5l3 3 7-7" />
    </svg>
  );
}
