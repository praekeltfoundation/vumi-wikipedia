from setuptools import setup, find_packages

setup(
    name='vumi-wikipedia',
    version='dev',
    description='Vumi Wikipedia App',
    packages=find_packages(),
    install_requires=[
        'vumi > 0.3.1',
        'BeautifulSoup',
    ],

    url='http://github.com/praekelt/vumi-wikipedia',
    license='BSD',
    long_description=open('README', 'r').read(),
    maintainer='Praekelt Foundation',
    maintainer_email='dev@praekelt.com',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: BSD License',
        'Operating System :: POSIX',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
        'Topic :: Software Development :: Libraries :: Python Modules',
        'Topic :: System :: Networking',
    ],
)
