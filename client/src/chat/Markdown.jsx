import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

const plugins = [remarkGfm];
const components = {
  a: ({ node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
};

// Raw HTML is intentionally not enabled. ReactMarkdown also filters unsafe URLs.
export default memo(function Markdown({ children, className = '', remarkPlugins = [] }) {
  return (
    <div className={`markdown ${className}`}>
      <ReactMarkdown remarkPlugins={remarkPlugins.length ? [...plugins, ...remarkPlugins] : plugins} components={components} skipHtml>
        {children ?? ''}
      </ReactMarkdown>
    </div>
  );
});
