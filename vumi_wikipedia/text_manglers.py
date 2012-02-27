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


def strip_html(text):
    """Strip HTML from MediaWiki content.

    Since we're trying to convert to text, we strip all newlines from the
    content and then add our own newlines when we see the appropriate elements.
    At the end, we normalize whitespace.

    The input likely contains elements that, while textual, are either useless
    cruft (such as edit links) or only make sense when attached to non-text
    elements (such as image captions or reference pointers). We exclude these
    based on CSS class.
    """
    from BeautifulSoup import BeautifulSoup, NavigableString, Comment

    CSS_CLASSES_TO_IGNORE = set([
            'thumbcaption',  # Caption text for thumbnail images.
            'editsection',  # Caption text for thumbnail images.
            'reference',  # Superscript reference pointer.
            ])

    HEADING_TAGS = set(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])
    NEWLINE_TAGS = set(['p', 'br'])

    def parse_tag(tag):
        output = []

        if tag.get('class', None) in CSS_CLASSES_TO_IGNORE:
            return []

        for child in tag.contents:
            if isinstance(child, Comment):
                continue
            if isinstance(child, NavigableString):
                output.append(child.replace('\n', ' '))
            else:
                output.extend(parse_tag(child))

        if tag.name in HEADING_TAGS:
            output.append(':\n')
        if tag.name in NEWLINE_TAGS:
            output.append('\n')

        return output

    soup = BeautifulSoup(text, convertEntities=["html", "xml"])
    text = ''.join(parse_tag(soup)).strip()
    text = '\n'.join([normalize_whitespace(l) for l in text.split('\n')])
    return text
