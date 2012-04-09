# -*- coding=utf-8 -*-

from twisted.trial.unittest import TestCase

from vumi_wikipedia.text_manglers import (mangle_text, convert_unicode,
    normalize_whitespace, is_unicode, truncate_sms, truncate_sms_with_postfix)


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

    def test_is_unicode(self):
        self.assertFalse(is_unicode(u'@foo^bar!'))
        self.assertTrue(is_unicode(u'foobar \n превед'))

    def test_truncate_sms(self):
        self.assertEquals(u'', truncate_sms(u''))
        self.assertEquals(u'foo...', truncate_sms(u'foo bar', 6, 3))
        self.assertEquals(u'Спасибо Пукину...',
            truncate_sms(u'Спасибо Пукину за это', 30, 15))

    def test_truncate_sms_with_postfix(self):
        self.assertEquals(u'foo bar... (for more madness, program in Python)',
            truncate_sms_with_postfix(u'foo bar baz',
            u' (for more madness, program in Python)', 46, 23))
        self.assertEquals(u'хрень какая-то... (testetstest)',
            truncate_sms_with_postfix(u'хрень какая-то нахреначилася',
            u' (testetstest)', 60, 30))
        self.assertEquals(u'foo bar... (превед)',
            truncate_sms_with_postfix(u'foo bar baz',
            u' (превед)', 32, 16))
