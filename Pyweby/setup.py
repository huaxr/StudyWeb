# coding=utf-8
from setuptools import setup

setup(
    name="Pyweby",
    version="1.0",
    author="huaxr",
    author_email="787518771@qq.com",
    description=("Very Sexy Web Framework. savvy?"),
    license="GPLv3",
    url="https://github.com/huaxr/Pyweby",

    install_requires=[
        'pymysql',
        'concurrent',
        'future',
        'redis>=2.10.5',
    ],
)