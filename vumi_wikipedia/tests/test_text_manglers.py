from twisted.trial.unittest import TestCase, SkipTest

from vumi_wikipedia.text_manglers import (
    mangle_text, convert_unicode, normalize_whitespace, strip_html)


class TextManglersTestCase(TestCase):

    def test_mangle_text(self):
        rev = lambda t: ''.join(reversed(t))
        upp = lambda t: t.upper()
        add = lambda t: ''.join([t, 'd'])
        self.assertEqual(u'abc', mangle_text(u'abc'))
        self.assertEqual(u'cba', mangle_text(u'abc', [rev]))
        self.assertEqual(u'abc', mangle_text(u'abc', [rev, rev]))
        self.assertEqual(u'CBA', mangle_text(u'abc', [rev, upp]))
        self.assertEqual(u'dCBA', mangle_text(u'abc', [upp, add, rev]))
        self.assertEqual(u'CBAD', mangle_text(u'abc', [rev, add, upp]))

    def test_convert_unicode(self):
        self.assertEqual(u'a-b c', convert_unicode(u'a\u2013b\xa0c'))

    def test_normalize_whitespace(self):
        self.assertEqual(u'a b c', normalize_whitespace(u'\ta  b\n c\r'))

    def test_strip_html(self):
        try:
            strip_html('')
        except ImportError:
            raise SkipTest("BeautifulSoup not installed.")

        assert_strip_html = lambda e, h: self.assertEqual(e, strip_html(h))

        assert_strip_html(u'a b c', u'<a>a</a>\nb c')
        assert_strip_html(u'ac', u'a<!-- b -->c')
        assert_strip_html(u'c', u'<a class="reference"><p>a</p>b</a>c')
        assert_strip_html(u'a b', u'a&#160;b')
