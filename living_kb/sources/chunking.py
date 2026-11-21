def _overlap_word_count(chunk: list[str], chunk_overlap: int) -> int:
    characters = 0
    for count, word in enumerate(reversed(chunk), start=1):
        characters += len(word) + 1
        if characters >= chunk_overlap:
            return count
    return len(chunk)


def chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be non-negative and smaller than chunk_size")

    words = text.split()
    if not words:
        return []

    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = start
        length = len(words[start])
        if length > chunk_size:
            chunks.append(words[start])
            start += 1
            continue
        while end + 1 < len(words) and length + 1 + len(words[end + 1]) <= chunk_size:
            end += 1
            length += 1 + len(words[end])
        chunks.append(" ".join(words[start : end + 1]))
        if end == len(words) - 1:
            break
        overlap = min(_overlap_word_count(words[start : end + 1], chunk_overlap), end - start)
        start = end - overlap + 1
    return chunks
