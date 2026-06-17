/* ============================================================
   AI Hub — Shared Markdown Renderer (no library)
   Used by: static/index.html (main chat) + static/admin.html (Chat Monitor)
   Exposes: window.AIHUB_MARKDOWN = { render, highlightAll }
   ============================================================
   Supports: **bold**, *italic*, `inline code`, ```fenced code (lang)```,
   # h1-h6, - lists, 1. ordered, [text](url), > blockquote, --- hr.
   XSS-safe: protects < and & in user content with placeholders, does
   markdown transformations (which add real <tags>), then restores
   placeholders to safe HTML entities.
   Syntax highlight: optional — if window.hljs is present, call
   AIHUB_MARKDOWN.highlightAll(container) to highlight <pre><code> blocks.
   ============================================================ */
(function () {
    'use strict';

    function render(text) {
        if (!text) return '';
        // 1. Extract fenced code blocks with optional language → placeholders
        const codeBlocks = [];
        let s = text.replace(/```([a-zA-Z0-9_+-]*)\n?([\s\S]*?)```/g, (_, lang, code) => {
            codeBlocks.push({ lang: (lang || '').toLowerCase(), code: code.replace(/^\n|\n$/g, '') });
            return `\x00CODE${codeBlocks.length - 1}\x00`;
        });
        // 2. Inline code → placeholders (same pool)
        s = s.replace(/`([^`\n]+)`/g, (_, code) => {
            codeBlocks.push({ lang: '', code });
            return `\x00CODE${codeBlocks.length - 1}\x00`;
        });
        // 3. Protect < and & in user content (so generated tags aren't escaped)
        s = s.replace(/&/g, '\x00AMP\x00').replace(/</g, '\x00LT\x00');
        // 4. Block-level markdown
        s = s.replace(/^###### (.*$)/gim, '<h6>$1</h6>')
             .replace(/^##### (.*$)/gim, '<h5>$1</h5>')
             .replace(/^#### (.*$)/gim, '<h4>$1</h4>')
             .replace(/^### (.*$)/gim, '<h3>$1</h3>')
             .replace(/^## (.*$)/gim, '<h2>$1</h2>')
             .replace(/^# (.*$)/gim, '<h1>$1</h1>');
        s = s.replace(/^---+$/gim, '<hr>');
        s = s.replace(/^> (.*$)/gim, '<blockquote>$1</blockquote>');
        // 5. Unordered lists — group consecutive "- " lines
        s = s.replace(/(^|\n)((?:^- .*(?:\n|$))+)/gm, (m, pre, block) => {
            const items = block.trim().split('\n').map(l => l.replace(/^- /, '')).join('</li><li>');
            return `${pre}<ul><li>${items}</li></ul>`;
        });
        // 6. Ordered lists — group consecutive "1. " lines
        s = s.replace(/(^|\n)((?:^\d+\. .*(?:\n|$))+)/gm, (m, pre, block) => {
            const items = block.trim().split('\n').map(l => l.replace(/^\d+\. /, '')).join('</li><li>');
            return `${pre}<ol><li>${items}</li></ol>`;
        });
        // 7. Inline markdown
        s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
             .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
        s = s.replace(/\[([^\]]+)\]\(((?:https?:\/\/)[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
        // 8. Restore code placeholders (HTML-escape code content now)
        s = s.replace(/\x00CODE(\d+)\x00/g, (_, i) => {
            const entry = codeBlocks[+i];
            const escaped = entry.code
                .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            if (entry.code.includes('\n')) {
                const langClass = entry.lang ? ` class="language-${entry.lang}"` : '';
                return `<pre><code${langClass}>${escaped}</code></pre>`;
            }
            return `<code>${escaped}</code>`;
        });
        // 9. Restore protected user-content entities
        s = s.replace(/\x00AMP\x00/g, '&amp;').replace(/\x00LT\x00/g, '&lt;');
        // 10. Line breaks (preserves user-entered newlines as <br>)
        s = s.replace(/\n/g, '<br>');
        return s;
    }

    /* Apply syntax highlighting to all <pre><code> blocks under `container`.
       Requires window.hljs (highlight.js) — silently no-op if absent. */
    function highlightAll(container) {
        if (typeof window === 'undefined' || !window.hljs) return;
        const root = container || document;
        const blocks = root.querySelectorAll ? root.querySelectorAll('pre code') : [];
        blocks.forEach((block) => {
            try { window.hljs.highlightElement(block); } catch (_) { /* ignore */ }
        });
    }

    window.AIHUB_MARKDOWN = { render, highlightAll };
})();
