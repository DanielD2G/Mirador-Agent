"""Regenerate the invented typeerror-was-waf eval case with a fixed seed."""

import asyncio

from _incidents import generate

if __name__ == "__main__":
    asyncio.run(generate("typeerror-was-waf"))
