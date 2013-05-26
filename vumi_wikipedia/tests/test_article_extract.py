from twisted.trial.unittest import TestCase

from vumi_wikipedia.article_extract import ArticleExtract


class SectionMarkerCreator(object):
    def __getitem__(self, key):
        return u'\ufffd\ufffd%s\ufffd\ufffd' % (key,)


def make_extract(text):
    return ArticleExtract(text % SectionMarkerCreator())


class ArticleExtractTestCase(TestCase):
    def assert_titles(self, ae, *titles):
        self.assertEqual(list(titles), [s.title for s in ae.sections])

    def assert_texts(self, ae, *texts):
        self.assertEqual(list(texts), [s.text for s in ae.sections])

    def assert_full_texts(self, ae, *texts):
        self.assertEqual(list(texts), [s.full_text() for s in ae.sections])

    def assert_section(self, section, title, text):
        self.assertEqual(title, section.title)
        self.assertEqual(text, section.text)

    def test_one_section(self):
        ae = make_extract(u'foo\nbar')
        self.assert_titles(ae, None)
        self.assert_texts(ae, u'foo\nbar')

    def test_multiple_sections(self):
        ae = make_extract(u'foo\n\n\n%(2)s bar \nbaz\n%(2)squux\n\n\nlol')
        self.assert_titles(ae, None, u'bar', u'quux')
        self.assert_texts(ae, u'foo', u'baz', u'lol')

    def test_shallow_nested_sections(self):
        ae = make_extract(u'%(2)sfoo\n%(3)s bar \ntext\n%(3)s baz\nblah')
        self.assert_titles(ae, None, u'foo')
        self.assert_texts(ae, u'', u'')
        self.assert_full_texts(ae, u'', u'bar:\n\ntext\n\nbaz:\n\nblah')

        [s20, s21] = ae.sections[1].get_subsections()
        self.assert_section(s20, u'bar', u'text')
        self.assert_section(s21, u'baz', u'blah')

    def test_deep_nested_sections(self):
        ae = make_extract('\n'.join([
                    u'%(2)ss1\nt1',
                    u'%(3)ss20\nt20',
                    u'%(3)ss21\nt21',
                    u'%(4)ss30\nt30',
                    u'%(4)ss31\nt31',
                    u'%(3)ss22\nt22',
                    ]))
        self.assert_titles(ae, None, u's1')
        self.assert_texts(ae, u'', u't1')
        self.assert_full_texts(ae, u'', '\n\n'.join([
                    u't1',
                    u's20:\n\nt20',
                    u's21:\n\nt21',
                    u's30:\n\nt30',
                    u's31:\n\nt31',
                    u's22:\n\nt22']))

        [intro, s1] = ae.sections
        [s20, s21, s22] = s1.get_subsections()
        [s30, s31] = s21.get_subsections()

        self.assertEqual([], intro.get_subsections())
        self.assertEqual([], s20.get_subsections())
        self.assertEqual([], s30.get_subsections())
        self.assertEqual([], s31.get_subsections())
        self.assertEqual([], s22.get_subsections())

        self.assert_section(intro, None, u'')
        self.assert_section(s1, u's1', u't1')
        self.assert_section(s20, u's20', u't20')
        self.assert_section(s21, u's21', u't21')
        self.assert_section(s30, u's30', u't30')
        self.assert_section(s31, u's31', u't31')
        self.assert_section(s22, u's22', u't22')

    def test_empty_input(self):
        ae = ArticleExtract(u'')
        self.assertEqual([u''], [s.text for s in ae.sections])
        self.assertEqual([None], [s.title for s in ae.sections])
