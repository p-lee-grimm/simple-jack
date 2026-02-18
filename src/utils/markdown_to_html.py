"""Convert markdown from Claude to Telegram-compatible HTML."""

import re
import html
from typing import Tuple


def markdown_to_telegram_html(text: str) -> str:
    """
    Convert markdown to Telegram HTML format.

    Supported conversions:
    - ```lang\\ncode\\n``` -> <pre><code class="language-lang">code</code></pre>
    - `code` -> <code>code</code>
    - **bold** -> <b>bold</b>
    - *italic* -> <i>italic</i>
    - _italic_ -> <i>italic</i>
    - ***bold italic*** -> <b><i>bold italic</i></b>
    - ~~strikethrough~~ -> <s>strikethrough</s>
    - [text](url) -> <a href="url">text</a>
    - # headings -> <b>heading</b>
    - - item / * item -> bullet lists
    - 1. item -> numbered lists
    - Escaped chars: \\*, \\_, \\~, \\`, \\[
    """
    placeholders = []

    def _placeholder(replacement: str) -> str:
        idx = len(placeholders)
        placeholders.append(replacement)
        return f"\x00PH{idx}\x00"

    # Phase 0: Protect escaped characters (\_  \*  \~  \`  \[)
    # Replace \X with a placeholder containing the literal character
    def replace_escaped(match):
        char = match.group(1)
        return _placeholder(html.escape(char))

    text = re.sub(r'\\([*_~`\[\]\\])', replace_escaped, text)

    # Phase 1: Extract fenced code blocks (``` ... ```)
    def replace_fenced_code(match):
        lang = match.group(1) or ""
        code = match.group(2)
        escaped_code = html.escape(code)
        if lang:
            replacement = f'<pre><code class="language-{html.escape(lang)}">{escaped_code}</code></pre>'
        else:
            replacement = f'<pre><code>{escaped_code}</code></pre>'
        return _placeholder(replacement)

    # Match ``` with optional language, content, closing ```
    # The closing ``` can be at end of string or followed by newline
    text = re.sub(r'```(\w*)\n(.*?)```', replace_fenced_code, text, flags=re.DOTALL)

    # Phase 1b: Extract markdown tables and render as monospace <pre> blocks
    def replace_table(match):
        table_text = match.group(0)
        lines = table_text.strip().split('\n')

        # Parse rows (skip separator line)
        rows = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('|') and stripped.endswith('|'):
                # Check if separator row (all dashes/colons/spaces/pipes)
                inner = stripped[1:-1]
                if re.match(r'^[\s|:\-]+$', inner):
                    continue
                cells = [c.strip() for c in inner.split('|')]
                rows.append(cells)

        if not rows:
            return table_text

        # Calculate column widths
        num_cols = max(len(row) for row in rows)
        col_widths = [0] * num_cols
        for row in rows:
            for i, cell in enumerate(row):
                if i < num_cols:
                    col_widths[i] = max(col_widths[i], len(cell))

        # Format as fixed-width text
        formatted_lines = []
        for row_idx, row in enumerate(rows):
            parts = []
            for i in range(num_cols):
                cell = row[i] if i < len(row) else ""
                parts.append(cell.ljust(col_widths[i]))
            formatted_lines.append("  ".join(parts))
            # Add separator after header
            if row_idx == 0:
                sep_parts = ["\u2500" * w for w in col_widths]
                formatted_lines.append("  ".join(sep_parts))

        table_str = html.escape('\n'.join(formatted_lines))
        return _placeholder(f'<pre>{table_str}</pre>')

    # Match markdown tables: lines starting with | that have at least a header and separator
    text = re.sub(
        r'(?:^\|.+\|[ \t]*\n){2,}(?:^\|.+\|[ \t]*\n?)*',
        replace_table,
        text,
        flags=re.MULTILINE,
    )

    # Phase 2: Extract inline code (` ... `)
    def replace_inline_code(match):
        code = match.group(1)
        escaped_code = html.escape(code)
        return _placeholder(f'<code>{escaped_code}</code>')

    text = re.sub(r'`([^`\n]+)`', replace_inline_code, text)

    # Phase 3: Convert bullet lists BEFORE escaping HTML
    # - item or * item at line start (with optional leading spaces)
    text = re.sub(r'^([ \t]*)[-*]\s+', r'\1• ', text, flags=re.MULTILINE)

    # Phase 4: Links BEFORE escaping (URLs contain & and other chars)
    # Support balanced parentheses in URLs: [text](url(with_parens))
    def replace_link(match):
        link_text = match.group(1)
        url = match.group(2)
        escaped_text = html.escape(link_text)
        escaped_url = html.escape(url, quote=True)
        return _placeholder(f'<a href="{escaped_url}">{escaped_text}</a>')

    text = re.sub(r'\[([^\]]+)\]\(((?:[^()]+|\([^()]*\))+)\)', replace_link, text)

    # Phase 4b: Convert <br> tags to newlines (Claude sometimes outputs these)
    text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)

    # Phase 5: Escape HTML in remaining text
    text = html.escape(text, quote=False)

    # Phase 6: Apply inline formatting
    # Bold+Italic: ***text*** -> <b><i>text</i></b>
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<b><i>\1</i></b>', text)

    # Bold: **text**
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)

    # Italic: *text* (not bullet, not inside bold)
    text = re.sub(r'(?<!\w)\*([^*\n]+?)\*(?!\*)', r'<i>\1</i>', text)

    # Italic: _text_
    text = re.sub(r'(?<!\w)_([^_\n]+?)_(?!\w)', r'<i>\1</i>', text)

    # Strikethrough: ~~text~~
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)

    # Phase 7: Headings -> bold text
    text = re.sub(r'^#{1,6}\s+(.+)$', r'<b>\1</b>', text, flags=re.MULTILINE)

    # Remove horizontal rules
    text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)

    # Phase 8: Numbered lists
    text = re.sub(r'^(\d+)\.\s+', r'<b>\1.</b> ', text, flags=re.MULTILINE)

    # Phase 9: Restore placeholders
    for i, replacement in enumerate(placeholders):
        text = text.replace(f"\x00PH{i}\x00", replacement)

    return text.strip()


def safe_markdown_to_html(text: str) -> Tuple[str, str]:
    """
    Convert markdown to HTML with fallback.

    Returns:
        (converted_text, "HTML") on success
        (escaped_text, "HTML") on failure (just escaping, no formatting)
    """
    try:
        result = markdown_to_telegram_html(text)
        return result, "HTML"
    except Exception:
        return html.escape(text), "HTML"
