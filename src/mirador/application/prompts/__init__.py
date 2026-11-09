from importlib.resources import files
from typing import Literal


def load_prompt(
    name: Literal["safety", "plan", "subagent", "completeness", "reporter", "judge"],
) -> str:
    root = files(__package__)
    safety = root.joinpath("safety.md").read_text()
    return (
        safety
        if name == "safety"
        else safety + "\n" + root.joinpath(name + ".md").read_text()
    )
