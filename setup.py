#!/usr/bin/env python

import os
import sys

from setuptools import setup, find_packages
from setuptools.command.test import test as TestCommand


class PyTest(TestCommand):
    user_options = [('pytest-args=', 'a', "Arguments to pass to py.test")]

    def initialize_options(self):
        TestCommand.initialize_options(self)
        self.pytest_args = []

    def finalize_options(self):
        TestCommand.finalize_options(self)
        self.test_args = []
        self.test_suite = True

    def run_tests(self):
        import pytest
        errno = pytest.main(self.pytest_args)
        sys.exit(errno)


readme = 'README.md'
if os.path.exists('README.rst'):
    readme = 'README.rst'
with open(readme) as f:
    long_description = f.read()

with open('requirements.txt') as f:
    requirements = [l for l in f.read().splitlines() if l]


setup(
    name='prerender',
    version='0.7.4',
    author='messense',
    author_email='messense@icloud.com',
    packages=find_packages(exclude=('tests', 'tests.*')),
    keywords='prerender',
    description='Render JavaScript-rendered page as HTML using headless Chrome',
    long_description=long_description,
    install_requires=requirements,
    include_package_data=True,
    tests_require=['pytest'],
    extras_require={
        'diskcache': ['diskcache'],
        's3': ['minio'],
    },
    cmdclass={'test': PyTest},
    entry_points='''
        [console_scripts]
        prerender=prerender.cli:main
    ''',
    classifiers=[
        'Programming Language :: Python',
        'Programming Language :: Python :: 3.6',
        'Programming Language :: Python :: Implementation :: CPython',
        'Topic :: Utilities',
    ]
)
