"""Shared tokenisation so retrieval, grounding and diagnosis agree on what a 'term' is."""

import re

STOPWORDS = frozenset(
    "a an and are as at be been but by can could do does for from had has have how "
    "i if in into is it its may my of on or our so such than that the their them "
    "then there these they this to was we were what when where which who why will "
    "with within would you your s t d ll re ve m don doesn didn isn aren wasn weren won".split()
)
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
WORD = re.compile(r"[a-z0-9]+")


def stem(word):
    """Deliberately tiny: fold plurals only, so 'days' matches 'day'."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokens(text):
    """Lowercased, stemmed words and numbers with stopwords removed (keeps repeats)."""
    return [stem(w) for w in WORD.findall(text.lower()) if w not in STOPWORDS]


def terms(text):
    return set(tokens(text))


def numbers(text):
    """Numbers as comparable strings: '1,000' and '1000' are the same value."""
    return {n.replace(",", "").rstrip(".") for n in NUMBER.findall(text)}
