"""Smart message splitting for Telegram."""

from typing import List
import re


MAX_MESSAGE_LENGTH = 4096


def split_message(text: str, is_html: bool = False) -> List[str]:
    """
    Split long messages into chunks respecting Telegram's 4096 character limit.

    Preserves code blocks and paragraph boundaries.
    When is_html=True, tracks <pre><code> blocks instead of ``` markers.
    """
    if len(text) <= MAX_MESSAGE_LENGTH:
        return [text]

    # Reserve space for part number headers like "[xx/xx]\n\n" (up to 12 chars)
    PART_HEADER_OVERHEAD = 12
    effective_max = MAX_MESSAGE_LENGTH - PART_HEADER_OVERHEAD

    chunks = []
    current_chunk = ""
    in_code_block = False
    code_block_tag = ""  # The opening tag to reopen across chunks

    lines = text.split('\n')

    for i, line in enumerate(lines):
        # Detect code block boundaries
        if is_html:
            opens_code = bool(re.search(r'<pre(?:>|<code)', line))
            closes_code = bool(re.search(r'</pre>', line))
        else:
            opens_code = bool(re.match(r'^```(\w*)\s*$', line))
            closes_code = bool(re.match(r'^```\s*$', line)) and in_code_block

        # Determine if this line is a good split point (paragraph boundary outside code)
        is_blank = line.strip() == ""
        is_good_split = is_blank and not in_code_block

        # Calculate length if we add this line
        separator = "\n" if current_chunk else ""
        potential_length = len(current_chunk) + len(separator) + len(line)

        if potential_length <= effective_max:
            # Fits — just append
            current_chunk = current_chunk + separator + line
        else:
            # Doesn't fit — flush current chunk
            if current_chunk:
                if in_code_block:
                    # Close the code block at chunk boundary
                    if is_html:
                        current_chunk += "\n</code></pre>"
                    else:
                        current_chunk += "\n```"
                chunks.append(current_chunk)

                # Reopen code block in new chunk
                if in_code_block:
                    current_chunk = code_block_tag + "\n" + line
                else:
                    current_chunk = line
            else:
                # Single line too long — hard split
                while len(line) > effective_max:
                    chunks.append(line[:effective_max])
                    line = line[effective_max:]
                current_chunk = line

        # Update code block tracking AFTER processing the line
        if opens_code and not closes_code:
            in_code_block = True
            if is_html:
                match = re.search(r'(<pre><code[^>]*>|<pre>)', line)
                code_block_tag = match.group(1) if match else '<pre><code>'
            else:
                m = re.match(r'^```(\w*)', line)
                code_block_tag = '```' + (m.group(1) if m else '')
        elif closes_code:
            in_code_block = False

    # Flush remaining
    if current_chunk:
        if in_code_block:
            if is_html:
                current_chunk += "\n</code></pre>"
            else:
                current_chunk += "\n```"
        chunks.append(current_chunk)

    # Add part numbers if multiple chunks
    if len(chunks) > 1:
        total = len(chunks)
        chunks = [f"[{i+1}/{total}]\n\n{chunk}" for i, chunk in enumerate(chunks)]

    return chunks
