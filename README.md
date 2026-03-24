# Runpod Image Generation Client

Small Python client for sending prompt-based image generation jobs to a Runpod Serverless endpoint.

This project uses:

- a workflow template in `input.json`
- prompts from `list_inputs.txt`
- async Runpod job submission and polling
- local saving of debug responses and output images

## Files

```text
main.py
requirements.txt
.env
.gitignore
input.json
list_inputs.txt
outputs/
