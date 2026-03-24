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
```

## Requirements
Python 3.10+
A Runpod Serverless endpoint
A valid Runpod API key
A valid workflow request template in input.json

## Installation

### Windows PowerShell
```text
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Windows CMD
```text
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### WSL / Linux / macOS
```text
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Environment Variables

Create a .env file in the project root:
```text
RUNPOD_API_KEY=your_runpod_api_key
RUNPOD_ENDPOINT_ID=your_endpoint_id
Important
```

Do not commit .env.
Rotate your API key if it was ever exposed.
