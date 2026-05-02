import { useEffect, useState } from "react";
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
 */
export function MarkdownText({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        code({ className, children, ...rest }) {
          const lang = /language-(\w+)/.exec(className || "")?.[1];
          const code = String(children).replace(/\n$/, "");
          if (!lang) {
            return (
              <code className="md-inline-code" {...rest}>
                {children}
              </code>
            );
          }
          return <ShikiCode code={code} lang={lang} />;
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
}

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

  if (html === null) {
    return (
      <pre className="md-code-fallback">
        <code>{code}</code>
      </pre>
    );
  }
  return <div className="md-code" dangerouslySetInnerHTML={{ __html: html }} />;
}
