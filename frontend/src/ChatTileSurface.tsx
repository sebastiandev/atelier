import {
  forwardRef,
  type FormHTMLAttributes,
  type HTMLAttributes,
  type ReactNode,
} from "react";

import { TileHeader } from "./TileHeader";

function cx(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(" ");
}

type ChatTileFrameProps = HTMLAttributes<HTMLDivElement> & {
  headerLeft: ReactNode;
  headerMeta?: ReactNode;
  headerRight?: ReactNode;
  headerProps?: HTMLAttributes<HTMLElement>;
  bodyClassName?: string;
};

export function ChatTileFrame({
  headerLeft,
  headerMeta,
  headerRight,
  headerProps,
  bodyClassName,
  className,
  children,
  ...divProps
}: ChatTileFrameProps) {
  return (
    <div {...divProps} className={cx("agent-tile chat-tile mode-tile", className)}>
      <TileHeader
        {...headerProps}
        className={headerProps?.className}
        left={headerLeft}
        meta={headerMeta}
        right={headerRight}
      />
      <div className={cx("agent-tile-body chat-tile-body", bodyClassName)}>
        {children}
      </div>
    </div>
  );
}

export const ChatTileTranscript = forwardRef<
  HTMLDivElement,
  HTMLAttributes<HTMLDivElement>
>(function ChatTileTranscript({ className, ...props }, ref) {
  return (
    <div
      {...props}
      ref={ref}
      className={cx("transcript chat-tile-transcript", className)}
    />
  );
});

export function ChatTileComposer({
  className,
  ...props
}: FormHTMLAttributes<HTMLFormElement>) {
  return (
    <form {...props} className={cx("composer chat-tile-composer", className)} />
  );
}
