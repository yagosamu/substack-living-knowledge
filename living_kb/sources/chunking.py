def chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = start + 1
        length = len(words[start])
        while end < len(words) and length + 1 + len(words[end]) <= chunk_size:
            length += 1 + len(words[end])
            end += 1
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            break

        next_start = end
        overlap_length = 0
        while next_start > start + 1:
            word_length = len(words[next_start - 1]) + 1
            if overlap_length + word_length > chunk_overlap:
                break
            overlap_length += word_length
            next_start -= 1
        start = next_start

    return chunks
