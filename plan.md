# Goal

A oci container that can be run on a raspberry pi in a trusted network that talks to the official trmnl home-assistant integration and updates a e-paper screen from waveshare conntected to the pi.
constraints:
- runs on a rasperry pi 3
- talks to the e-ink panel via spi
- waveshare documentation and examples in the docs/waveshare folder
- must do partial updates
- talks to and is being talked to the official trmnl ha integration for image generation: https://github.com/usetrmnl/trmnl-home-assistant
- supports pull and push mode from the ha integration
- contains linting and tests
- is documented in markdown in the docs folder. graphics in mermaidjs.
- container is built in a github workflow and pushed to the registry belonging to the github project

