from setuptools import find_packages, setup

setup(
    name="browser-agent",
    version="0.1.0",
    description="DOM-driven Playwright CLI browser agent",
    packages=find_packages(),
    install_requires=[
        "google-generativeai>=0.8.0",
        "PyYAML>=6.0.1",
    ],
    python_requires=">=3.11",
    entry_points={"console_scripts": ["browser-agent=browser_agent.main:main"]},
)
