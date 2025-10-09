import requests
import base64
import json
from PIL import Image
from io import BytesIO
import matplotlib.pyplot as plt
import numpy as np
import sys
import threading
from typing import Dict, Any, Tuple, Optional, List

# =========================
# Config
# =========================
API_URL = "http://example.xyz:7861"
IMAGE_URL = "http://example.xyz:7869/uploads/flux-image.png"
NEW_PROMPT = "a dog is drinking out of a bowl on the surface of mars"
TIMEOUT = 1200000  # seconds

# Default to the combo that worked for you:
DEFAULT_VAE = "None"        # per-request override (lets FLUX use its own AE)
DEFAULT_SAMPLER = "Euler a"
DEFAULT_SCHEDULER = "simple"  # some backends ignore this; harmless if so
DEFAULT_CFG = 3.5
DEFAULT_STEPS = 20

# =========================
# HTTP helpers
# =========================
def request_get(url: str, **kw) -> requests.Response:
    r = requests.get(url, timeout=kw.get("timeout", TIMEOUT), params=kw.get("params"))
    r.raise_for_status()
    return r

def request_post_json(url: str, payload: Dict[str, Any], timeout: int = TIMEOUT) -> Dict[str, Any]:
    r = requests.post(url, json=payload, timeout=timeout)
    try:
        data = r.json()
    except Exception:
        sys.stderr.write(f"Non-JSON response from {url}\nHTTP {r.status_code}\nBody (first 2KB):\n{r.text[:2048]}\n")
        r.raise_for_status()
        raise
    if not r.ok:
        sys.stderr.write(f"HTTP {r.status_code} error from {url}:\n{json.dumps(data, indent=2)[:2048]}\n")
        r.raise_for_status()
    return data

# =========================
# Parsing helpers
# =========================
def parse_int(x, default):
    try:
        return int(x)
    except Exception:
        try:
            return int(float(x))
        except Exception:
            return default

def parse_parameters_maybe_string(p):
    if isinstance(p, dict):
        return p
    if isinstance(p, str):
        try:
            j = json.loads(p)
            if isinstance(j, dict):
                return j
        except Exception:
            pass
        out = {}
        for line in p.replace("\r", "").split("\n"):
            line = line.strip()
            if not line or ":" not in line:
                continue
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
        return out
    return {}

def size_from_params(p) -> Tuple[int, int]:
    size = p.get("Size") or p.get("size")
    if isinstance(size, str) and "x" in size.lower():
        w, h = size.lower().split("x", 1)
        return parse_int(w, 512), parse_int(h, 512)
    return parse_int(p.get("Size-1", 512), 512), parse_int(p.get("Size-2", 512), 512)

def strip_data_url_prefix(b64: str) -> str:
    return b64.split(",", 1)[1] if b64.startswith("data:image") else b64

# =========================
# Image utils
# =========================
def flatten_to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, img.convert("RGBA")).convert("RGB")
    if img.mode == "P":
        return img.convert("RGBA").convert("RGB")
    if img.mode != "RGB":
        return img.convert("RGB")
    return img

def looks_all_black(img: Image.Image) -> bool:
    arr = np.asarray(img.convert("L"))
    mean_val = float(arr.mean())
    print(f"[debug] grayscale mean intensity ~ {mean_val:.4f}")
    return mean_val < 1.0

# =========================
# Progress polling
# =========================
def poll_progress(stop_event: threading.Event):
    """
    Polls /sdapi/v1/progress while generation runs in the main thread.
    Prints a single updating line with % / step / job / eta.
    """
    while not stop_event.is_set():
        try:
            r = request_get(f"{API_URL}/sdapi/v1/progress",
                            params={"skip_current_image": True},
                            timeout=10)
            j = r.json()
            p = j.get("progress") or 0.0
            eta = j.get("eta_relative")
            state = j.get("state") or {}
            sstep = state.get("sampling_step")
            ssteps = state.get("sampling_steps")
            job_no = state.get("job_no")
            job_count = state.get("job_count")

            parts = [f"[progress] {int(p * 100):3d}%"]
            if sstep is not None and ssteps:
                parts.append(f"step {sstep}/{ssteps}")
            if job_no is not None and job_count:
                parts.append(f"job {job_no}/{job_count}")
            if eta is not None:
                parts.append(f"eta ~{int(eta)}s")
            line = " | ".join(parts)
            print(line.ljust(80), end="\r", flush=True)
        except Exception:
            # keep polling silently
            pass
        stop_event.wait(0.5)
    # clear the progress line
    print(" " * 80, end="\r")

# =========================
# Generation (single attempt)
# =========================
def generate_with_progress(payload: Dict[str, Any], attempt_id: int) -> Optional[Image.Image]:
    stop_event = threading.Event()
    t = threading.Thread(target=poll_progress, args=(stop_event,), daemon=True)
    t.start()
    try:
        result = request_post_json(f"{API_URL}/sdapi/v1/txt2img", payload)
    finally:
        stop_event.set()
        t.join(timeout=2)

    info_field = result.get("info") or ""
    if info_field:
        try:
            j = json.loads(info_field)
            print("\n[debug] model:", j.get("sd_model_name"),
                  "| sampler:", j.get("sampler_name"),
                  "| scheduler:", j.get("scheduler") or "<default>",
                  "| sd_vae_name:", j.get("sd_vae_name"))
        except Exception:
            print("\n[debug] info (raw, truncated):", info_field[:4000])

    images = result.get("images")
    if not images:
        print("[warn] backend returned no images")
        return None

    b64 = strip_data_url_prefix(images[0])
    raw = base64.b64decode(b64)
    fname = f"generated_attempt{attempt_id}.png"
    with open(fname, "wb") as f:
        f.write(raw)
    print(f"[attempt {attempt_id}] saved raw image -> {fname}")

    try:
        img = Image.open(BytesIO(raw))
    except Exception as e:
        print("[warn] PIL failed to open image:", e)
        return None

    img = flatten_to_rgb(img)
    if looks_all_black(img):
        print(f"[attempt {attempt_id}] ⚠️ still black/very dark.")
        return None

    img.save("generated_view.png", format="PNG", optimize=True)
    print(f"[attempt {attempt_id}] ✅ non-black. Saved -> generated_view.png")
    return img

# =========================
# Main
# =========================
def main():
    # Pull size & hints from metadata image
    print("Downloading image for metadata...")
    img_resp = request_get(IMAGE_URL)
    img_b64 = base64.b64encode(img_resp.content).decode()

    print("Extracting metadata via /sdapi/v1/png-info ...")
    meta = request_post_json(f"{API_URL}/sdapi/v1/png-info", {"image": img_b64})
    params = parse_parameters_maybe_string(meta.get("parameters", {}) or {})
    print("Original parameters (parsed, truncated):")
    try:
        print(json.dumps(params, indent=2)[:4000])
    except Exception:
        print(params)

    width, height = size_from_params(params)
    steps = parse_int(params.get("Steps", DEFAULT_STEPS), DEFAULT_STEPS)
    negative_prompt = params.get("Negative prompt", "")

    # ---------- Attempt 1: the known-good default for your setup ----------
    attempt = 1
    base_payload = {
        "prompt": NEW_PROMPT,
        "negative_prompt": negative_prompt,
        "width": width,
        "height": height,
        "seed": -1,
        "steps": max(steps, DEFAULT_STEPS),
        "cfg_scale": DEFAULT_CFG,
        "sampler_name": DEFAULT_SAMPLER,
        # Some backends honor this for schedule selection; ignored otherwise
        "scheduler": DEFAULT_SCHEDULER,
        # Critical: let FLUX use its own AE
        "override_settings": {"sd_vae": DEFAULT_VAE},
        "override_settings_restore_after": True,
        # Keep accidental paths off
        "enable_hr": False,
        "denoising_strength": None,
        "init_images": None,
        "mask": None,
        "clip_skip": 1,
    }

    print(f"\n[attempt {attempt}] Defaulting to VAE={DEFAULT_VAE!r}, sampler={DEFAULT_SAMPLER!r}, "
          f"scheduler={DEFAULT_SCHEDULER!r}, cfg={DEFAULT_CFG}, steps={base_payload['steps']}")
    img = generate_with_progress(base_payload, attempt_id=attempt)

    # ---------- Fallbacks if still black ----------
    if img is None:
        print("\n[info] Running small fallback grid…")
        attempt += 1
        vae_opts: List[str] = ["Automatic", "None"]  # try switching between explicit None and backend default
        sampler_opts = [DEFAULT_SAMPLER, "Euler"]
        cfg_opts = [DEFAULT_CFG, 5.0]
        step_opts = [base_payload["steps"], 28]
        sched_opts = [DEFAULT_SCHEDULER, ""]

        for vae in vae_opts:
            for sampler in sampler_opts:
                for sched in sched_opts:
                    for cfg in cfg_opts:
                        for st in step_opts:
                            payload = dict(base_payload)
                            payload["override_settings"] = {"sd_vae": vae}
                            payload["sampler_name"] = sampler
                            payload["scheduler"] = sched
                            payload["cfg_scale"] = cfg
                            payload["steps"] = st
                            print(f"\n[attempt {attempt}] VAE={vae!r} sampler={sampler!r} "
                                  f"scheduler={sched!r} cfg={cfg} steps={st}")
                            img = generate_with_progress(payload, attempt_id=attempt)
                            if img is not None:
                                break
                            attempt += 1
                        if img is not None: break
                    if img is not None: break
                if img is not None: break
            if img is not None: break

        if img is None:
            raise RuntimeError("All attempts produced black images. Likely VAE/model mismatch or a backend issue.")

    # ---------- Display ----------
    fig_w = max(5, img.size[0] / 200)
    fig_h = max(5, img.size[1] / 200)
    plt.figure(figsize=(fig_w, fig_h), facecolor="white")
    plt.imshow(img)
    plt.axis("off")
    plt.title(f"Generated Image ({img.size[0]}x{img.size[1]})")
    plt.tight_layout()
    plt.show()
    print("Done!")

if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        sys.stderr.write(f"HTTP error: {e}\n")
        sys.exit(1)
    except requests.RequestException as e:
        sys.stderr.write(f"Network error: {e}\n")
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"Unexpected error: {e}\n")
        sys.exit(1)
