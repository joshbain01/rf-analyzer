"""Setuptools packaging for rf-monitor."""

from setuptools import find_packages, setup

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="rf-monitor",
    version="1.0.0",
    author="RF Monitor Contributors",
    description="RF spectrum monitoring and jamming detection using RTL-SDR",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/example/rf-monitor",
    packages=find_packages(exclude=["tests*"]),
    python_requires=">=3.8",
    install_requires=requirements,
    extras_require={
        "dev": [
            "pytest>=7.0",
            "pytest-cov>=4.0",
        ],
    },
    entry_points={
        "console_scripts": [
            "rf-monitor=rf_monitor.cli:main",
        ],
        # Extension point for custom analyzers
        "rf_monitor.analyzers": [],
        # Extension point for custom alert handlers
        "rf_monitor.alert_handlers": [],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: Console",
        "Intended Audience :: Science/Research",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Topic :: Scientific/Engineering",
        "Topic :: System :: Monitoring",
    ],
    keywords="rtl-sdr rf spectrum monitoring jamming detection radar",
)
