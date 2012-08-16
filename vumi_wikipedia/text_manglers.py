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


def truncate_content(content, postfix=None, more_postfix=False,
                     ascii_limit=160, unicode_limit=70, ellipsis=u'...'):
    """Smart string truncation
    """
    def over_limit(text):
        limit = unicode_limit if is_unicode(text) else ascii_limit
        limit -= len(ellipsis)
        if postfix:
            limit -= len(postfix)
        return len(text) > limit

    content_length = 0
    result = u''

    words = content.split(' ')

    while words:
        word = words.pop(0)
        longer_string = (result + ' ' + word).strip()
        if over_limit(longer_string):
            content_length = len(result)
            result = result + ellipsis
            break
        result = longer_string
        content_length = len(result)

    if postfix is not None:
        if (len(words) > 0) or (not more_postfix):
            result += postfix
    return content_length, result


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
