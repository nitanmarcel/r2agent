from importlib.resources import files


def load_prompt(name: str) -> str:
    prompt_file = files("r2agent.prompts").joinpath(f"{name}.txt")
    return prompt_file.read_text()
