import base64
import concurrent.futures
import copy
import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

ENDPOINT_ID = os.environ["RUNPOD_ENDPOINT_ID"]
API_KEY = os.environ["RUNPOD_API_KEY"]

RUN_URL = f"https://api.runpod.ai/v2/{ENDPOINT_ID}/run"
STATUS_URL = f"https://api.runpod.ai/v2/{ENDPOINT_ID}/status"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

INPUT_LIST_FILE = "list_inputs.txt"
WORKFLOW_TEMPLATE_FILE = "example_input.json"
OUTPUT_DIR = "outputs"

IMAGES_PER_INPUT = 1
MAX_CONCURRENT_REQUESTS = 5
POLL_INTERVAL_SECONDS = 3
REQUEST_TIMEOUT_SECONDS = 60
STATUS_TIMEOUT_SECONDS = 60
MAX_WAIT_SECONDS = 1800


def log(message: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}")


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_prompts(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def set_prompt(payload: dict, prompt: str) -> dict:
    data = copy.deepcopy(payload)
    data["input"]["workflow"]["1"]["inputs"]["text"] = prompt
    return data


def sanitize_filename(text: str, max_len: int = 80) -> str:
    cleaned = "".join(c if c.isalnum() or c in (" ", "_", "-") else "_" for c in text)
    cleaned = "_".join(cleaned.split())
    return cleaned[:max_len] or "output"


def submit_job(payload: dict) -> str:
    response = requests.post(
        RUN_URL,
        headers=HEADERS,
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    result = response.json()

    if "id" not in result:
        raise RuntimeError(f"Unexpected submit response: {json.dumps(result, indent=2)}")

    return result["id"]


def get_job_status(job_id: str) -> dict:
    response = requests.get(
        f"{STATUS_URL}/{job_id}",
        headers=HEADERS,
        timeout=STATUS_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def wait_for_completion(job_id: str) -> dict:
    start_time = time.time()

    while True:
        result = get_job_status(job_id)
        status = result.get("status", "UNKNOWN")

        if status in {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}:
            return result

        if time.time() - start_time > MAX_WAIT_SECONDS:
            raise TimeoutError(f"Job {job_id} exceeded max wait time.")

        time.sleep(POLL_INTERVAL_SECONDS)


def save_base64_image(image_b64: str, output_path: Path) -> None:
    image_bytes = base64.b64decode(image_b64)
    output_path.write_bytes(image_bytes)


def extract_images(result: dict) -> list[str]:
    output = result.get("output", {})

    if isinstance(output, dict):
        images = output.get("images", [])
        if isinstance(images, list) and images:
            extracted = []
            for item in images:
                if isinstance(item, dict) and "image" in item:
                    extracted.append(item["image"])
                elif isinstance(item, str):
                    extracted.append(item)
            return extracted

    return []


def save_result_images(result: dict, prompt: str, output_dir: Path, job_id: str) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)

    images = extract_images(result)
    if not images:
        raise RuntimeError(
            f"No images found in completed result for job {job_id}: {json.dumps(result, indent=2)}"
        )

    saved_files = []
    prompt_stub = sanitize_filename(prompt)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    for index, image_b64 in enumerate(images, start=1):
        output_path = output_dir / f"{timestamp}_{prompt_stub}_{job_id}_{index}.png"
        save_base64_image(image_b64, output_path)
        saved_files.append(output_path)

    return saved_files


def process_prompt(prompt: str, workflow_template: dict) -> dict:
    payload = set_prompt(workflow_template, prompt)

    job_id = submit_job(payload)
    log(f"Submitted job {job_id} for prompt: {prompt}")

    result = wait_for_completion(job_id)
    status = result.get("status", "UNKNOWN")

    if status != "COMPLETED":
        return {
            "prompt": prompt,
            "job_id": job_id,
            "status": status,
            "error": result,
            "saved_files": [],
        }

    saved_files = save_result_images(result, prompt, Path(OUTPUT_DIR), job_id)

    return {
        "prompt": prompt,
        "job_id": job_id,
        "status": status,
        "error": None,
        "saved_files": [str(path) for path in saved_files],
    }


def main() -> None:
    workflow_template = load_json(WORKFLOW_TEMPLATE_FILE)
    prompts = load_prompts(INPUT_LIST_FILE)

    if not prompts:
        raise ValueError("No prompts found in list_inputs.txt")

    expanded_prompts = []
    for prompt in prompts:
        for _ in range(IMAGES_PER_INPUT):
            expanded_prompts.append(prompt)

    log(f"Loaded {len(prompts)} prompt(s)")
    log(f"Submitting {len(expanded_prompts)} job(s)")

    results = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENT_REQUESTS) as executor:
        future_map = {
            executor.submit(process_prompt, prompt, workflow_template): prompt
            for prompt in expanded_prompts
        }

        for future in concurrent.futures.as_completed(future_map):
            prompt = future_map[future]
            try:
                result = future.result()
                results.append(result)

                if result["status"] == "COMPLETED":
                    log(f"Completed: {prompt}")
                    for file_path in result["saved_files"]:
                        log(f"Saved: {file_path}")
                else:
                    log(f"Failed: {prompt}")
                    log(json.dumps(result["error"], indent=2))

            except Exception as exc:
                log(f"Unhandled error for prompt '{prompt}': {exc}")

    summary_path = Path(OUTPUT_DIR) / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    log(f"Summary saved to: {summary_path}")


if __name__ == "__main__":
    main()