# r2agent setup

## Requirements

* Python 3.10 or higher with pipx or pip
* radare2

## Installation

0. Install the latest r2agent release with pip or pipx `pipx install git+https://github.com/nitanmarcel/r2agent.git@main`
    *  for dev builds install r2agent with pip or pipx `pipx install git+https://github.com/nitanmarcel/r2agent.git@dev`
1. Install the radare2 plugin `r2pm -Uci r2agent`
2. Run r2agent `r2a -c` to generate the default config.yaml
3. Update your config to your liking. See [configuration][./configuration.md]
