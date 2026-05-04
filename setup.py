from setuptools import setup, find_packages
setup(
    name="quantfly",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "fastapi", "uvicorn", "pandas", "numpy", "requests", "aiohttp", "playwright",
    ],
    python_requires=">=3.10",
)
