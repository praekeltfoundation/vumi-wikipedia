from setuptools import setup, find_packages

setup(
    name='vumi-wikipedia',
    version='dev',
    description='Vumi Wikipedia App',
    packages=find_packages(),
    # package_data={'twisted.plugins': ['twisted/plugins/*.py']},
    # include_package_data=True,

    install_requires=[
        'vumi > 0.3.1',
        'BeautifulSoup',
    ],
    dependency_links=[
        # We need a newer Vumi than is on PyPI
        'https://github.com/praekelt/vumi/zipball/develop#egg=vumi-0.4.0a',
    ],

    url='http://github.com/praekelt/vumi-wikipedia',
    long_description=open('README', 'r').read(),
    maintainer='Praekelt Foundation',
    maintainer_email='dev@praekelt.com',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'Operating System :: POSIX',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: System :: Networking',
    ],
)
