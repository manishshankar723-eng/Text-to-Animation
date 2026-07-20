"""
smoke_test_providers.py — Verify image-generation backends before a full run.

Makes ONE minimal real image call to each backend (Vertex AI and/or Gemini
Developer API) and reports whether auth, the model name, and image output all
work. Use this after changing keys/models or before a costly full pipeline run.

Usage:
    # Test both backends:
    python smoke_test_providers.py

    # Test just one:
    python smoke_test_providers.py --provider gemini

    # Use your own reference image + save the returned image:
    python smoke_test_providers.py --image ./kamla.jpg --save

Exit code is 0 only if every tested provider succeeded (handy for CI/gating).
"""

import argparse
import io
import logging
import os
import sys

from dotenv import load_dotenv
from PIL import Image

load_dotenv()

# Import after load_dotenv so the client reads env correctly.
from google.genai import types  # noqa: E402
import gemini_client as gc  # noqa: E402

logger = logging.getLogger("smoke_test")

SCRATCH_DEFAULT = "output/_smoke"
# A short, filter-safe prompt that reliably produces an image.
TEST_PROMPT = (
    "Create a simple 2x2 grid of four plain solid-colored squares "
    "(red, green, blue, yellow) on a pure white background."
)


def _load_reference(image_path: str | None) -> Image.Image:
    """Load the given image, else the project sample, else synthesize one."""
    candidates = [image_path] if image_path else []
    candidates.append(os.path.join("Image_Ref", "kamla.jpg.png"))

    for path in candidates:
        if path and os.path.exists(path):
            logger.info("Using reference image: %s", path)
            return Image.open(path).convert("RGB")

    # Fallback: a plain 768×768 image so we always have something to send.
    logger.info("No reference image found — using a synthesized plain image.")
    return Image.new("RGB", (768, 768), (230, 230, 230))


def _hint_for_error(err: str) -> str | None:
    """Return an actionable hint for common failure messages."""
    e = err.lower()
    if "404" in e or "not_found" in e or "not found" in e:
        return (
            "Model name may be wrong for this backend. Override it via "
            "VERTEX_IMAGE_MODEL / GEMINI_IMAGE_MODEL in .env."
        )
    if "api key" in e or "api_key" in e or "gemini_api_key" in e or "permission" in e:
        return "Check GEMINI_API_KEY (for gemini) or your GCP auth (for vertex)."
    if "default credentials" in e or "adc" in e or "could not automatically determine" in e:
        return "Run: gcloud auth application-default login  (Vertex AI uses ADC)."
    if "429" in e or "resource_exhausted" in e or "quota" in e:
        return "Rate/quota limit hit — the backend is reachable but throttled."
    if "permission_denied" in e or "403" in e:
        return "Backend reachable but the account lacks access to this model/project."
    return None


def test_provider(provider: str, reference: Image.Image, save: bool) -> dict:
    """Make one real image call and return a result dict."""
    result = {"provider": provider, "ok": False, "model": None, "detail": ""}
    print(f"\n=== Testing provider: {provider} ===")

    try:
        model_id = gc._model_id(provider)
        result["model"] = model_id
        client = gc.get_client(provider)  # may raise (missing key / auth)
        print(f"  client OK   | model = {model_id}")
    except Exception as e:  # noqa: BLE001
        result["detail"] = f"client init failed: {e}"
        print(f"  client FAIL | {e}")
        hint = _hint_for_error(str(e))
        if hint:
            print(f"  hint: {hint}")
        return result

    try:
        response = client.models.generate_content(
            model=model_id,
            contents=[TEST_PROMPT, reference],
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )

        image = None
        if (
            response.candidates
            and response.candidates[0].content
            and response.candidates[0].content.parts
        ):
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.data:
                    image = Image.open(io.BytesIO(part.inline_data.data)).convert("RGB")
                    break

        if image is None:
            result["detail"] = "call returned no image (content filter or text-only response)"
            print(f"  call FAIL   | {result['detail']}")
            return result

        result["ok"] = True
        result["detail"] = f"got image {image.width}x{image.height}"
        print(f"  call OK     | {result['detail']}")

        if save:
            os.makedirs(SCRATCH_DEFAULT, exist_ok=True)
            out = os.path.join(SCRATCH_DEFAULT, f"smoke_{provider}.png")
            image.save(out, "PNG")
            print(f"  saved       | {out}")

        return result

    except Exception as e:  # noqa: BLE001
        result["detail"] = str(e)
        print(f"  call FAIL   | {e}")
        hint = _hint_for_error(str(e))
        if hint:
            print(f"  hint: {hint}")
        return result


def main():
    parser = argparse.ArgumentParser(
        description="Smoke-test image generation backends (Vertex AI / Gemini API).",
    )
    parser.add_argument(
        "--provider", choices=["vertex", "gemini", "both"], default="both",
        help="Which backend(s) to test (default: both).",
    )
    parser.add_argument(
        "--image", help="Reference image to send (default: sample or synthesized).",
    )
    parser.add_argument(
        "--save", action="store_true",
        help=f"Save each returned image to {SCRATCH_DEFAULT}/.",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    providers = ["vertex", "gemini"] if args.provider == "both" else [args.provider]
    reference = _load_reference(args.image)

    results = [test_provider(p, reference, args.save) for p in providers]

    # --- Summary ---
    print("\n" + "=" * 52)
    print("SMOKE TEST SUMMARY")
    print("=" * 52)
    for r in results:
        status = "PASS" if r["ok"] else "FAIL"
        print(f"  {r['provider']:7} [{status}]  {r['model'] or '-'}  {r['detail']}")
    print("=" * 52)

    all_ok = all(r["ok"] for r in results)
    if not all_ok:
        print("One or more providers failed. See hints above.")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
