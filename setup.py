#!/usr/bin/env python
from setuptools import setup

setup(
    name="tap-ga4",
    version="0.0.1",
    description="Singer.io tap for extracting data",
    author="Stitch",
    url="http://singer.io",
    classifiers=["Programming Language :: Python :: 3 :: Only"],
    py_modules=["tap_ga4"],
    install_requires=[
        "google-analytics-data",
        "singer-python==5.12.2",
        "requests==2.28.1",
        "backoff==1.8.0",
    ],
    extras_require={
        'test': [
            'pylint',
            'nose'
        ],
        'dev': [
            'ipdb',
            'pylint'
        ]
    },
    entry_points="""
    [console_scripts]
    tap-ga4=tap_ga4:main
    """,
    packages=["tap_ga4"],
)
