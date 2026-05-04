import { memo, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { codeToHtml } from "shiki";

/**
 * Renders markdown text with syntax-highlighted code fences.
 *
 * Used by AgentTile for assistant + thinking blocks. Claude emits
 * markdown directly in TextBlock content; the SDK doesn't pre-parse it.
 *
 * Streaming-safe: react-markdown handles partial markdown gracefully.
 *
 * Wrapped in `memo` because AgentTile re-renders on every composer
 * keystroke (`draft` state); without it, every transcript unit's
 * markdown gets re-parsed and `ShikiCode` re-applies its innerHTML,
 * which causes a visible flash on highlighted code blocks.
 */
export const MarkdownText = memo(function MarkdownText({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        // react-markdown calls this for both inline code and fenced
        // blocks. Block fences arrive multi-line (and react-markdown
        // wraps them in <pre>); inline arrives single-line. Branch on
        // that — plus the language hint — so a fence with no language
        // doesn't get stamped with the inline-pill style.
        code({ className, children, node, ...rest }) {
          void node; // strip — the AST node prop would otherwise leak
          // onto the DOM via {...rest} as `node="[object Object]"`.
          const lang = /language-(\w+)/.exec(className || "")?.[1];
          const text = String(children).replace(/\n$/, "");
          const isBlock = text.includes("\n") || Boolean(lang);
          if (isBlock) {
            if (lang) return <ShikiCode code={text} lang={lang} />;
            return (
              <pre className="md-code-fallback">
                <code>{text}</code>
              </pre>
            );
          }
          return (
            <code className="md-inline-code" {...rest}>
              {children}
            </code>
          );
        },
        // Default <pre> wraps our `code` output, but the block branch
        // above already returns its own <pre>. Make this transparent
        // so we don't get nested <pre><pre>.
        pre({ children }) {
          return <>{children}</>;
        },
        h1: ({ children }) => <h3 className="md-h">{children}</h3>,
        h2: ({ children }) => <h4 className="md-h">{children}</h4>,
        h3: ({ children }) => <h5 className="md-h">{children}</h5>,
        a: ({ children, href }) => (
          <a className="md-link" href={href} target="_blank" rel="noreferrer">
            {children}
          </a>
        ),
      }}
    >
      {text}
    </ReactMarkdown>
  );
});

const SHIKI_THEME = "github-dark";

function ShikiCode({ code, lang }: { code: string; lang: string }) {
  const [html, setHtml] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    codeToHtml(code, { lang, theme: SHIKI_THEME })
      .then((out) => {
        if (!cancelled) setHtml(out);
      })
      .catch(() => {
        if (!cancelled) setHtml(null);
      });
    return () => {
      cancelled = true;
    };
  }, [code, lang]);

  // Stabilise the innerHTML payload on `html` so React doesn't re-apply
  // identical markup on every parent re-render — that's what made the
  // highlighted code blocks visibly flash while typing in the composer.
  const inner = useMemo(() => (html === null ? null : { __html: html }), [html]);

  if (inner === null) {
    return (
      <pre className="md-code-fallback">
        <code>{code}</code>
      </pre>
    );
  }
  return <div className="md-code" dangerouslySetInnerHTML={inner} />;
}
