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
WORKFLOW_TEMPLATE_FILE = "input.json"
OUTPUT_DIR = Path("outputs")
DEBUG_DIR = OUTPUT_DIR / "debug"

IMAGES_PER_INPUT = 1
MAX_CONCURRENT_REQUESTS = 1
POLL_INTERVAL_SECONDS = 3
REQUEST_TIMEOUT_SECONDS = 60
STATUS_TIMEOUT_SECONDS = 60
MAX_WAIT_SECONDS = 1800


def log(message: str) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {message}")


def ensure_dirs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_prompts(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def sanitize_filename(text: str, max_len: int = 80) -> str:
    cleaned = "".join(c if c.isalnum() or c in (" ", "_", "-") else "_" for c in text)
    cleaned = "_".join(cleaned.split())
    return cleaned[:max_len] or "output"


def save_json_debug(name: str, data: dict) -> None:
    ensure_dirs()
    path = DEBUG_DIR / f"{name}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def set_prompt(payload: dict, prompt: str) -> dict:
    data = copy.deepcopy(payload)
    data["input"]["workflow"]["1"]["inputs"]["text"] = prompt
    return data


def submit_job(payload: dict, prompt: str) -> str:
    response = requests.post(
        RUN_URL,
        headers=HEADERS,
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    result = response.json()

    debug_name = f"submit_{sanitize_filename(prompt)}_{int(time.time())}"
    save_json_debug(debug_name, result)

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


def wait_for_completion(job_id: str, prompt: str) -> dict:
    start_time = time.time()
    last_status = None

    while True:
        result = get_job_status(job_id)
        status = result.get("status", "UNKNOWN")

        if status != last_status:
            log(f"Job {job_id} status: {status}")
            last_status = status

        if status in {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}:
            debug_name = f"final_{sanitize_filename(prompt)}_{job_id}"
            save_json_debug(debug_name, result)
            return result

        if time.time() - start_time > MAX_WAIT_SECONDS:
            raise TimeoutError(f"Job {job_id} exceeded max wait time.")

        time.sleep(POLL_INTERVAL_SECONDS)


def save_base64_image(image_b64: str, output_path: Path) -> None:
    image_bytes = base64.b64decode(image_b64)
    output_path.write_bytes(image_bytes)


def extract_image_entries(result: dict) -> list[dict]:
    output = result.get("output", {})

    if not isinstance(output, dict):
        return []

    images = output.get("images", [])
    if not isinstance(images, list):
        return []

    extracted = []
    for item in images:
        if isinstance(item, dict):
            extracted.append(item)
        elif isinstance(item, str):
            extracted.append({"data": item})

    return extracted


def save_result_images(result: dict, prompt: str, job_id: str) -> list[str]:
    ensure_dirs()

    image_entries = extract_image_entries(result)
    if not image_entries:
        raise RuntimeError(
            f"No images found in completed result for job {job_id}: {json.dumps(result, indent=2)}"
        )

    saved_files = []
    prompt_stub = sanitize_filename(prompt)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    for index, item in enumerate(image_entries, start=1):
        output_path = OUTPUT_DIR / f"{timestamp}_{prompt_stub}_{job_id}_{index}.png"

        image_b64 = item.get("data") or item.get("image")
        if image_b64:
            save_base64_image(image_b64, output_path)
            saved_files.append(str(output_path))
        else:
            log(f"Image entry has no base64 payload for job {job_id}: {json.dumps(item)}")

    return saved_files


def process_prompt(prompt: str, workflow_template: dict) -> dict:
    try:
        payload = set_prompt(workflow_template, prompt)

        job_id = submit_job(payload, prompt)
        log(f"Submitted job {job_id} for prompt: {prompt}")

        result = wait_for_completion(job_id, prompt)
        status = result.get("status", "UNKNOWN")

        if status != "COMPLETED":
            return {
                "prompt": prompt,
                "job_id": job_id,
                "status": status,
                "saved_files": [],
                "result": result,
                "error_message": f"Job ended with status {status}",
            }

        saved_files = save_result_images(result, prompt, job_id)

        return {
            "prompt": prompt,
            "job_id": job_id,
            "status": status,
            "saved_files": saved_files,
            "result": result,
            "error_message": None,
        }

    except Exception as exc:
        return {
            "prompt": prompt,
            "job_id": None,
            "status": "SCRIPT_ERROR",
            "saved_files": [],
            "result": None,
            "error_message": str(exc),
        }


def main() -> None:
    ensure_dirs()

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
            result = future.result()
            results.append(result)

            if result["status"] == "COMPLETED":
                log(f"Completed: {prompt}")
                if result["saved_files"]:
                    for file_path in result["saved_files"]:
                        log(f"Saved: {file_path}")
                else:
                    log(f"No PNG files saved for prompt: {prompt}")
                    log("Check outputs/debug/ for the raw final response.")
            else:
                log(f"Failed: {prompt}")
                log(f"Error: {result['error_message']}")
                if result["result"] is not None:
                    log("Raw result saved in outputs/debug/")

    summary_path = OUTPUT_DIR / "summary.json"
    summary_path.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"Summary saved to: {summary_path}")


if __name__ == "__main__":
    main()