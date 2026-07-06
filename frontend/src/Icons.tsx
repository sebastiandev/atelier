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

// Folder — used by shared-folders rail rows.
export function FolderIcon({ size = 12, ...rest }: IconProps) {
  return (
    <svg
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinejoin="round"
      strokeLinecap="round"
      aria-hidden
      {...rest}
    >
      <path d="M2 5.5V11a1.5 1.5 0 0 0 1.5 1.5h9A1.5 1.5 0 0 0 14 11V6a1.5 1.5 0 0 0-1.5-1.5H8L6.5 3h-3A1.5 1.5 0 0 0 2 4.5v1z" />
    </svg>
  );
}

// Three-dot "more" affordance for trailing row icons.
export function MoreIcon({ size = 12, ...rest }: IconProps) {
  return (
    <svg
      viewBox="0 0 16 16"
      width={size}
      height={size}
      fill="currentColor"
      aria-hidden
      {...rest}
    >
      <circle cx="3" cy="8" r="1.3" />
      <circle cx="8" cy="8" r="1.3" />
      <circle cx="13" cy="8" r="1.3" />
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

export function EditIcon({ size = 14, ...rest }: IconProps) {
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
      <path d="M3 11.5V13h1.5l7-7-1.5-1.5-7 7z" />
      <path d="M9.5 5 11 3.5 12.5 5 11 6.5" />
    </svg>
  );
}

export function ChatIcon({ size = 14, ...rest }: IconProps) {
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
      <path d="M3.5 4.5A2.5 2.5 0 0 1 6 2h4a2.5 2.5 0 0 1 2.5 2.5v3A2.5 2.5 0 0 1 10 10H7l-3 2.5V10A2.5 2.5 0 0 1 1.5 7.5v-3" />
    </svg>
  );
}

export function MoveIcon({ size = 14, ...rest }: IconProps) {
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
      <path d="M2.5 8h7" />
      <path d="M7 5.5 9.5 8 7 10.5" />
      <path d="M10.5 3.5h2.5v9h-2.5" />
    </svg>
  );
}

export function BoltIcon({ size = 14, ...rest }: IconProps) {
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
      <path d="M9 1.8 3.8 8.5h3L6.8 14.2l5.4-7.4h-3L9 1.8z" />
    </svg>
  );
}

export function BranchIcon({ size = 14, ...rest }: IconProps) {
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
      <circle cx="4" cy="4" r="1.7" />
      <circle cx="12" cy="4" r="1.7" />
      <circle cx="8" cy="12" r="1.7" />
      <path d="M5.4 5.2 8 10.3M10.6 5.2 8 10.3" />
    </svg>
  );
}

export function BugIcon({ size = 14, ...rest }: IconProps) {
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
      <path d="M5.2 6.2a2.8 2.8 0 0 1 5.6 0v3.2a2.8 2.8 0 0 1-5.6 0V6.2z" />
      <path d="M6.2 3.2 5 2M9.8 3.2 11 2M3 7h2.2M10.8 7H13M3.5 11l1.8-1M10.7 10l1.8 1" />
      <path d="M6.5 6.5h3" />
    </svg>
  );
}

export function SparkIcon({ size = 14, ...rest }: IconProps) {
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
      <path d="M8 1.8l1.2 3 3 1.2-3 1.2L8 10.2 6.8 7.2l-3-1.2 3-1.2L8 1.8zM12.5 10.5l.6 1.4 1.4.6-1.4.6-.6 1.4-.6-1.4-1.4-.6 1.4-.6.6-1.4zM3.2 11.2l.4 1 .9.4-.9.4-.4 1-.4-1-.9-.4.9-.4.4-1z" />
    </svg>
  );
}

export function SendIcon({ size = 14, ...rest }: IconProps) {
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
      <path d="M2 8l11-5-3 11-2-4-4 3 3-4-5-1z" />
    </svg>
  );
}

export function DocIcon({ size = 14, ...rest }: IconProps) {
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
      <path d="M4 2.5h5l3 3v8H4z" />
      <path d="M9 2.5v3h3M6 8h6M6 10.5h6" />
    </svg>
  );
}

export function PaperclipIcon({ size = 14, ...rest }: IconProps) {
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
      <path d="M5.2 8.2 8.9 4.5a2.2 2.2 0 1 1 3.1 3.1l-5.1 5.1a3.4 3.4 0 0 1-4.8-4.8l5.2-5.2" />
      <path d="M6.1 10.1 11 5.2" />
    </svg>
  );
}

export function AgentIcon({ size = 14, ...rest }: IconProps) {
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
      <circle cx="8" cy="5" r="2" />
      <path d="M4.5 13a3.8 3.8 0 0 1 7 0" />
      <path d="M3 8.5h1.5M11.5 8.5H13" />
    </svg>
  );
}

export function EyeIcon({ size = 14, ...rest }: IconProps) {
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
      <path d="M2 8s2.2-4 6-4 6 4 6 4-2.2 4-6 4-6-4-6-4z" />
      <circle cx="8" cy="8" r="1.7" />
    </svg>
  );
}
