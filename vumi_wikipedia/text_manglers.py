# -*- test-case-name: vumi_wikipedia.tests.test_text_manglers -*-

import unicodedata


def mangle_text(text, manglers=()):
    for mangler in manglers:
        text = mangler(text)
    return text


def unicode_ord(name):
    return ord(unicodedata.lookup(name))


UNICODE_CONVERSION_MAPPING = {
    unicode_ord('EN DASH'): u'-',
    unicode_ord('EM DASH'): u'-',
    }


def convert_unicode(text):
    """Convert unicode characters to ASCII, where possible.

    First step is to normalize whatever we've been given using the
    compatibility decomposition and canonical recomposition (NFKC). Thereafter,
    we manually replace some extra characters.

    NOTE: This does not strip out all non-ASCII characters. Just some of them.
    """

    text = unicodedata.normalize('NFKC', text)
    return text.translate(UNICODE_CONVERSION_MAPPING)


def normalize_whitespace(text):
    """Replace each whitespace sequence with a single space.
    """
    return ' '.join(text.strip().split())
