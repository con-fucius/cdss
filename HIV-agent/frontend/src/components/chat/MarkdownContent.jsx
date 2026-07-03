import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { PrismLight as SyntaxHighlighter } from 'react-syntax-highlighter';
import bash from 'react-syntax-highlighter/dist/esm/languages/prism/bash';
import javascript from 'react-syntax-highlighter/dist/esm/languages/prism/javascript';
import json from 'react-syntax-highlighter/dist/esm/languages/prism/json';
import jsx from 'react-syntax-highlighter/dist/esm/languages/prism/jsx';
import python from 'react-syntax-highlighter/dist/esm/languages/prism/python';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';

SyntaxHighlighter.registerLanguage('bash', bash);
SyntaxHighlighter.registerLanguage('javascript', javascript);
SyntaxHighlighter.registerLanguage('js', javascript);
SyntaxHighlighter.registerLanguage('json', json);
SyntaxHighlighter.registerLanguage('jsx', jsx);
SyntaxHighlighter.registerLanguage('python', python);
SyntaxHighlighter.registerLanguage('py', python);

export function MarkdownContent({ content, onCiteClick }) {
  const components = {
    code({ inline, className, children, ...props }) {
      const match = /language-(\w+)/.exec(className || '');
      const codeString = String(children).replace(/\n$/, '');

      if (!inline && match) {
        return (
          <SyntaxHighlighter
            style={oneDark}
            language={match[1]}
            PreTag="div"
            customStyle={{
              margin: '12px 0',
              borderRadius: '6px',
              fontSize: '12px',
            }}
            {...props}
          >
            {codeString}
          </SyntaxHighlighter>
        );
      }
      return (
        <code className={className} {...props}>
          {children}
        </code>
      );
    },
    table({ children }) {
      return (
        <div className="table-wrapper">
          <table className="markdown-table">{children}</table>
        </div>
      );
    },
    a({ href, children }) {
      const isCite = href && href.startsWith('#cite-');
      return (
        <a
          href={href}
          className={isCite ? 'cite-link' : ''}
          onClick={isCite ? (e) => {
            e.preventDefault();
            const idx = parseInt(href.replace('#cite-', ''));
            if (onCiteClick) onCiteClick(idx);
          } : undefined}
        >
          {children}
        </a>
      );
    }
  };

  let processedContent = content;
  const sourceRegex = /\[(\d+)\]/g;
  const sources = [];
  let match;
  while ((match = sourceRegex.exec(content)) !== null) {
    if (!sources.includes(match[1])) {
      sources.push(match[1]);
      processedContent = processedContent.replace(
        match[0],
        `[${match[1]}](#cite-${match[1]})`
      );
    }
  }

  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={components}
      >
        {processedContent}
      </ReactMarkdown>
    </div>
  );
}

export function SourcesDisplay({ sources, onCiteClick, sourceBaseId = 'source' }) {
  if (!sources || sources.length === 0) return null;

  const uniqueSources = [];
  const seen = new Set();
  for (const source of sources) {
    const sourceText = typeof source === 'string' ? source : source.source;
    const key = typeof source === 'string'
      ? source
      : source.chunk_id || source.parent_id || sourceText;
    if (!key || seen.has(key)) continue;
    seen.add(key);
    uniqueSources.push(source);
  }

  const getDiseaseColor = (disease) => {
    switch (disease?.toLowerCase()) {
      case 'hiv': return 'var(--primary-color)';
      case 'diabetes': return '#e67e22';
      case 'cvd': return '#e74c3c';
      case 'tb': return '#8e44ad';
      default: return 'var(--text-secondary)';
    }
  };

  return (
    <div className="sources-container" role="region" aria-label={uniqueSources.length === 1 ? 'Source' : 'Sources'}>
      <div className="sources-header">{uniqueSources.length === 1 ? 'Source' : 'Sources'}</div>
      {uniqueSources.map((src, i) => {
        const sourceText = typeof src === 'string' ? src : src.source;
        const disease = typeof src === 'string' ? 'unknown' : src.disease;

        return (
          <div
            key={i}
            id={`${sourceBaseId}-${i + 1}`}
            className="source-item"
            style={{ borderLeftColor: getDiseaseColor(disease) }}
            onClick={() => onCiteClick && onCiteClick(i, src)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => e.key === 'Enter' && onCiteClick && onCiteClick(i, src)}
          >
            <span className="source-number" style={{ color: getDiseaseColor(disease) }}>[{i + 1}]</span>
            {src.low_confidence && <span className="source-low-confidence">Low confidence</span>}
            {sourceText.substring(0, 150)}{sourceText.length > 150 ? '...' : ''}
          </div>
        );
      })}
    </div>
  );
}
