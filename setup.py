from pathlib import Path

import setuptools

setuptools.setup(
    name="peewee_redshift",
    version="0.0.1",
    author="Christopher Boyd",
    description="An Amazon Redshift database extension for the Pewee ORM",
    long_description_content_type="text/markdown",
    long_description=Path('README.md').read_text(),
    url=None,
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.6',
    install_requires=['peewee']
)
