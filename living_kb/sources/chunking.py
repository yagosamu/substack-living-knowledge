def chunk_text(text: str, chunk_size: int, chunk_overlap: int) -> list[str]:
    if chunk_size < 1:
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
        length = 0
        while end < len(words):
            candidate = length + len(words[end]) + (1 if end > start else 0)
            if candidate > chunk_size and end > start:
                break
            length = candidate
            end += 1

        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            break

        overlap_length = 0
        next_start = end
        for index in range(end - 1, start - 1, -1):
            word_length = len(words[index]) + (1 if index < end - 1 else 0)
            if overlap_length + word_length > chunk_overlap:
                break
            overlap_length += word_length
            next_start = index
        start = max(start + 1, next_start)

    return chunks
