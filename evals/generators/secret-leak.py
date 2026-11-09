"""Regenerate the invented secret-leak eval case with a fixed seed."""

import asyncio

from _incidents import generate

if __name__ == "__main__":
    asyncio.run(generate("secret-leak"))
