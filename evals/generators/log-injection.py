"""Regenerate the invented log-injection eval case with a fixed seed."""

import asyncio

from _incidents import generate

if __name__ == "__main__":
    asyncio.run(generate("log-injection"))
