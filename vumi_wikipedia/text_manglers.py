# -*- test-case-name: vumi_wikipedia.tests.test_text_manglers -*-

import unicodedata
import re
try:
    from unidecode import unidecode
    unidecode  # To keep pyflakes happy
except ImportError:
    unidecode = None


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


def transliterate_unicode(text):
    """Convert unicode to the equivalent ASCII representation"""
    if unidecode is None:
        raise RuntimeError('Missing library, run "pip install unidecode"')
    return unidecode(text)


MINIMIZE_REGEX = [
    (re.compile(u' -+ | ?--+ ?'), '--'),
    ]


def minimize_unicode(text):
    """Remove as much as possible from the text without loosing text's meaning.
    Perform this after all other normalizations are done."""
    for regex, repl in MINIMIZE_REGEX:
        text = regex.sub(repl, text)
    return text


def normalize_whitespace(text):
    """Replace each whitespace sequence with a single space.
    """
    return ' '.join(text.strip().split())


UNICODE_REGEX = re.compile(u'[\u0080-\uffff]')


def is_unicode(string):
    return UNICODE_REGEX.search(string) is not None


class ContentFormatter(object):
    def __init__(self, ascii_limit, unicode_limit, pre_ellipsis=u'...',
                 post_ellipsis=u' ...', sentence_break_threshold=10):
        self.ascii_limit = ascii_limit
        self.unicode_limit = unicode_limit
        self.pre_ellipsis = pre_ellipsis
        self.post_ellipsis = post_ellipsis
        self.sentence_break_threshold = sentence_break_threshold

    def get_limit(self, text, extra_len):
        limit = self.unicode_limit if is_unicode(text) else self.ascii_limit
        return limit - extra_len

    def format_more(self, content, offset, more=u'', no_more=u''):
        extra_len = 0
        text = content

        if offset > 0:
            text = self.pre_ellipsis + content[offset:]
            extra_len += len(self.pre_ellipsis)

        if len(text) <= self.get_limit(text, len(no_more)):
            # Everything fits with the `no_more` text.
            return ((len(text) - extra_len), text + no_more)

        # It doesn't all fit, so we need ellipsis and `more`
        return self._format(text, more, extra_len)

    def _truncate_text(self, text, max_length):
        truncated_text = text.rsplit(None, 1)[0]
        if truncated_text == text:
            # We have a single long "word", so split it in the middle.
            truncated_text = text[:max_length]
        return truncated_text

    def _format(self, content, postfix, extra_len):
        text = content

        if len(text) <= self.get_limit(text, len(postfix)):
            # Everything fits with the `postfix` text.
            return ((len(text) - extra_len), text + postfix)

        # It doesn't all fit, so we need ellipsis and `postfix`
        postfix = self.post_ellipsis + postfix
        max_length = self.get_limit(text, len(postfix))
        while len(text) > max_length:
            text = self._truncate_text(text, max_length)

        # Try to break on sentence end if that won't cost too many characters.
        if self.sentence_break_threshold > 0:
            if '. ' in text[-self.sentence_break_threshold:]:
                text = text.rsplit('. ', 1)[0] + '.'

        return ((len(text) - extra_len), text + postfix)

    def format(self, content, postfix=u''):
        return self._format(content, postfix, 0)[1]
