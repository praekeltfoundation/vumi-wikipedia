# -*- test-case-name: vumi_wikipedia.tests.test_text_manglers -*-

import unicodedata
import re


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


UNICODE_REGEX = re.compile(u'[\u0080-\uffff]')


def is_unicode(string):
    return UNICODE_REGEX.search(string) is not None


class ContentFormatter(object):
    def __init__(self, ascii_limit, unicode_limit, pre_ellipsis=u'...',
                 post_ellipsis=u'...'):
        self.ascii_limit = ascii_limit
        self.unicode_limit = unicode_limit
        self.pre_ellipsis = pre_ellipsis
        self.post_ellipsis = post_ellipsis

    def get_limit(self, text, extra_len):
        limit = self.unicode_limit if is_unicode(text) else self.ascii_limit
        return limit - extra_len

    def format_more(self, content, offset, more='', no_more=''):
        extra_len = 0
        text = content

        if offset > 0:
            text = self.pre_ellipsis + content[offset:]
            extra_len += len(self.pre_ellipsis)

        if len(text) <= self.get_limit(text, len(no_more)):
            # Everything fits with the `no_more` text.
            return ((len(text) - extra_len), text + no_more)

        # It doesn't all fit, so we need ellipsis and `more`
        return self.format(text, more, extra_len)

    def format(self, content, postfix='', extra_len=0):
        text = content

        if len(text) <= self.get_limit(text, len(postfix)):
            # Everything fits with the `postfix` text.
            return ((len(text) - extra_len), text + postfix)

        # It doesn't all fit, so we need ellipsis and `postfix`
        postfix = self.post_ellipsis + postfix
        while len(text) > self.get_limit(text, len(postfix)):
            text = text.rsplit(None, 1)[0]

        # TODO: Sentence breaks, if possible.
        return ((len(text) - extra_len), text + postfix)


def truncate_sms(string, ascii_limit=160, unicode_limit=70, ellipsis=u'...'):
    """Smart string truncation
    """
    result = u''
    for word in string.split(' '):
        longer_string = (result + ' ' + word).strip()
        if (((is_unicode(longer_string) and len(longer_string) > unicode_limit)
             or (len(longer_string) > ascii_limit))):
                return result + ellipsis
        result = longer_string
    return result


def truncate_sms_with_postfix(string, postfix, ascii_limit=160,
                              unicode_limit=70, ellipsis=u'...'):
        length = len(postfix)
        if is_unicode(postfix):
            ascii_limit = unicode_limit
        return truncate_sms(string, ascii_limit - length,
            unicode_limit - length, ellipsis) + postfix
