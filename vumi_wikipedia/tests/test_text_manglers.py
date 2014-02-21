from twisted.trial.unittest import TestCase

from vumi_wikipedia.text_manglers import (convert_unicode,
    normalize_whitespace, is_unicode, ContentFormatter)


class TextManglersTestCase(TestCase):
    def test_convert_unicode(self):
        self.assertEqual(u'a-b c', convert_unicode(u'a\u2013b\xa0c'))

    def test_normalize_whitespace(self):
        self.assertEqual(u'a b c', normalize_whitespace(u'\ta  b\n c\r'))

    def test_is_unicode(self):
        self.assertFalse(is_unicode(u'@foo^bar!'))
        self.assertTrue(is_unicode(
                u'foobar \n \u043f\u0440\u0435\u0432\u0435\u0434'))


UNI_BIT = u'\u044d\u0442\u043e'


def long_ascii(bits=50, suffix=u''):
    return u' '.join([u'abc'] * bits) + suffix


def long_unicode(bits=50, suffix=u''):
    return u' '.join([UNI_BIT] * bits) + suffix


class ContentFormatterTestCase(TestCase):

    def test_format_simple(self):
        cf = ContentFormatter(160, 70)
        self.assertEqual(u'', cf.format(u''))
        self.assertEqual(u'a', cf.format(u'a'))
        self.assertEqual(UNI_BIT, cf.format(UNI_BIT))
        self.assertEqual(long_ascii(39, u' ...'), cf.format(long_ascii()))
        self.assertEqual(long_unicode(16, u' ...'), cf.format(long_unicode()))

    def test_format_postfix(self):
        cf = ContentFormatter(160, 70)
        self.assertEqual(u' (postfix)', cf.format(u'', u' (postfix)'))
        self.assertEqual(u'a (postfix)', cf.format(u'a', u' (postfix)'))
        self.assertEqual(u'%s (postfix)' % UNI_BIT,
                         cf.format(UNI_BIT, u' (postfix)'))
        self.assertEqual(long_ascii(36, u' ... (postfix)'),
                         cf.format(long_ascii(), u' (postfix)'))
        self.assertEqual(long_unicode(14, u' ... (postfix)'),
                         cf.format(long_unicode(), u' (postfix)'))

    def test_format_more(self):
        cf = ContentFormatter(160, 70)
        fmt = lambda txt, i: cf.format_more(txt, i, u' (more)', u' (no more)')

        self.assertEqual((0, u' (no more)'), fmt(u'', 0))
        self.assertEqual((1, u'a (no more)'), fmt(u'a', 0))
        self.assertEqual((3, u'%s (no more)' % UNI_BIT), fmt(UNI_BIT, 0))
        self.assertEqual((1, u'...a (no more)'), fmt(u'a a', 2))
        self.assertEqual((3, u'...%s (no more)' % UNI_BIT),
                         fmt(long_unicode(2), 4))

        self.assertEqual((147, long_ascii(37, u' ... (more)')),
                         fmt(long_ascii(), 0))
        self.assertEqual((59, long_unicode(15, u' ... (more)')),
                         fmt(long_unicode(), 0))

        self.assertEqual((143, u'...' + long_ascii(36, u' ... (more)')),
                         fmt(long_ascii(), 4))
        self.assertEqual((55, u'...' + long_unicode(14, u' ... (more)')),
                         fmt(long_unicode(), 4))
