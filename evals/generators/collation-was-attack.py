"""Regenerate the invented collation-was-attack eval case with a fixed seed."""

import asyncio

from _incidents import generate

if __name__ == "__main__":
    asyncio.run(generate("collation-was-attack"))
