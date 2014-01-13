from setuptools import setup, find_packages

setup(
    name='vumi-wikipedia',
    version='0.1a',
    description='Vumi Wikipedia App',
    packages=find_packages(),
    include_package_data=True,
    install_requires=[
        # NOTE: We currently depend on a developer version of vumi that must be
        # installed before us.
        'vumi>0.4',
        'unidecode',
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
