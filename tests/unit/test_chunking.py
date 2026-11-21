import pytest

from living_kb.sources.chunking import chunk_text


def test_short_text_stays_in_one_chunk() -> None:
    text = "durable pipelines resume where they stopped"
    assert chunk_text(text, chunk_size=1000, chunk_overlap=150) == [text]


def test_blank_text_produces_no_chunks() -> None:
    assert chunk_text("  \n\t ", chunk_size=1000, chunk_overlap=150) == []


def test_long_text_splits_into_word_aligned_chunks_that_repeat_the_previous_tail() -> None:
    # Every word is 4 characters, so 3 words + 2 spaces = 14 characters fill a chunk,
    # and one word + its space = 5 characters satisfies an overlap of 5.
    text = "aaaa bbbb cccc dddd eeee ffff gggg hhhh iiii jjjj"

    assert chunk_text(text, chunk_size=14, chunk_overlap=5) == [
        "aaaa bbbb cccc",
        "cccc dddd eeee",
        "eeee ffff gggg",
        "gggg hhhh iiii",
        "iiii jjjj",
    ]


def test_word_longer_than_chunk_size_becomes_its_own_chunk() -> None:
    text = "see https://example.com/a-very-long-url-that-cannot-be-split here"

    assert chunk_text(text, chunk_size=20, chunk_overlap=5) == [
        "see",
        "https://example.com/a-very-long-url-that-cannot-be-split",
        "here",
    ]


WORDS = [f"word{i:03d}" for i in range(300)]
MULTILINE_TEXT = "\n".join(" ".join(WORDS[i : i + 7]) for i in range(0, 300, 7))


def test_no_chunk_exceeds_chunk_size() -> None:
    chunks = chunk_text(MULTILINE_TEXT, chunk_size=100, chunk_overlap=20)

    assert len(chunks) > 1
    assert all(len(chunk) <= 100 for chunk in chunks)


def test_each_chunk_is_a_contiguous_run_of_whole_words() -> None:
    for chunk in chunk_text(MULTILINE_TEXT, chunk_size=100, chunk_overlap=20):
        chunk_words = chunk.split(" ")
        first = WORDS.index(chunk_words[0])
        assert chunk_words == WORDS[first : first + len(chunk_words)]


def test_chunks_cover_every_word() -> None:
    chunks = chunk_text(MULTILINE_TEXT, chunk_size=100, chunk_overlap=20)

    assert sorted({word for chunk in chunks for word in chunk.split(" ")}) == WORDS


@pytest.mark.parametrize("chunk_overlap", [100, 150])
def test_overlap_not_smaller_than_chunk_size_is_rejected(chunk_overlap: int) -> None:
    with pytest.raises(ValueError):
        chunk_text("some text", chunk_size=100, chunk_overlap=chunk_overlap)


def test_overlap_repeats_whole_words_until_it_reaches_chunk_overlap() -> None:
    # The last word of the first chunk ("z" + space = 2 chars) is short of an overlap
    # of 4, so the overlap keeps going back to "yy" (2 + 3 = 5 chars). It may exceed
    # chunk_overlap, but never falls short of it: no context is lost at a boundary.
    text = "xxxx yy z wwwwwww vv"

    assert chunk_text(text, chunk_size=12, chunk_overlap=4) == [
        "xxxx yy z",
        "yy z wwwwwww",
        "wwwwwww vv",
    ]
