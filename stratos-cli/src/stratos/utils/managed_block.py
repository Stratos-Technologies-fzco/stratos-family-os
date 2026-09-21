"""Stratos-managed blocks inside developer-owned Markdown files (pure functions)."""

BEGIN = "<!-- stratos:begin {id} -->"
END = "<!-- stratos:end {id} -->"


def set_managed_block(existing: str, block_id: str, content: str) -> str:
    """Return `existing` with the Stratos block `block_id` set to `content`.
    Everything outside the block is left exactly as it was."""
    begin, end = BEGIN.format(id=block_id), END.format(id=block_id)
    block = f"{begin}\n{content.strip()}\n{end}"
    if begin in existing and end in existing:
        head, rest = existing.split(begin, 1)
        _, tail = rest.split(end, 1)
        return head + block + tail
    return (existing.rstrip() + "\n\n" if existing.strip() else "") + block + "\n"
