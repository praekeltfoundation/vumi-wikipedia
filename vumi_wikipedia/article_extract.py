# -*- test-case-name: vumi_wikipedia.tests.test_article_extract -*-

import re
import json


ARTICLE_SPLITTER = re.compile(u'\ufffd\ufffd(?=\\d\ufffd\ufffd)')
ARTICLE_SECTION = re.compile(
    u'^(\\d)\ufffd\ufffd\\s*([^\\n]+?)\\s*(?:|\\n+(.*))$', re.DOTALL)


class ArticleExtract(object):
    """
    Class representing an article extract
    """

    def __init__(self, data):
        if isinstance(data, list):
            self.sections = data
        else:
            self._from_string(data)

    def _from_string(self, data):
        split_data = ARTICLE_SPLITTER.split(data)

        # Start building the section tree with the intro.
        self.sections = [ArticleSection(0, None, split_data[0].strip())]

        section_bits = []
        for section in split_data[1:]:
            section = section.strip()
            m = ARTICLE_SECTION.match(section)
            level, title, text = m.groups()
            level = int(level)
            if text is None:
                text = u''
            section_bits.append((level, title, text))

        # Not all section levels are used, so we renumber them for consistency.
        section_levels = list(sorted(set(s[0] for s in section_bits)))
        levels = dict((l, i) for i, l in enumerate(section_levels))

        for level, title, text in section_bits:
            level = levels[level]
            section = ArticleSection(level, title, text)
            if level == 0:
                self.sections.append(section)
            else:
                self.sections[-1].add_subsection(section)

    def to_json(self):
        return json.dumps([s.to_dict() for s in self.sections])

    @classmethod
    def from_json(cls, data):
        return cls([ArticleSection.from_dict(section)
                    for section in json.loads(data)])


class ArticleSection(object):
    def __init__(self, level, title, text):
        self.level = level
        self.title = title
        self.text = text
        self._subsections = []

    def add_subsection(self, subsection):
        if subsection.level > self.level + 1:
            if self._subsections:
                self._subsections[-1].add_subsection(subsection)
                return
        self._subsections.append(subsection)

    def get_subsections(self):
        # Return a shallow copy to avoid accidental mutation.
        return self._subsections[:]

    def __repr__(self):
        return '<%s: %r (%s)>' % (type(self).__name__, self.title, self.level)

    def full_text(self):
        text = self.text
        for section in self.get_subsections():
            if text:
                text += '\n\n'
            text += '%s:\n\n%s' % (section.title, section.full_text())
        return text

    def to_dict(self):
        return {
            'level': self.level,
            'title': self.title,
            'text': self.text,
            'sections': [s.to_dict() for s in self.get_subsections()],
            }

    @classmethod
    def from_dict(cls, data):
        section_extract = cls(data['level'], data['title'], data['text'])
        for subsection in data['sections']:
            section_extract.add_subsection(cls.from_dict(subsection))
        return section_extract
