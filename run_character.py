"""
run_character.py — CLI entry point for the character asset generation pipeline.

Usage:
    # Full run (all parts) with an uploaded image:
    python run_character.py --name kamla --image ./kamla.jpg --template saree --skip goggles,headphone

    # Generate reference from text prompt (Step 0) then run pipeline:
    python run_character.py --name kamla --prompt "Indian woman in red saree, age 30" --template saree

    # Test run (one part only, no GCS upload):
    python run_character.py --name kamla --image ./kamla.jpg --parts hair --local-only

    # With Meshy 3D generation:
    python run_character.py --name kamla --image ./kamla.jpg --template saree --meshy hair,face
"""

import argparse
import json
import logging
import sys

from dotenv import load_dotenv

# Load .env BEFORE importing pipeline modules (they read env vars at import time)
load_dotenv()

from pipeline import run_pipeline


def setup_logging(verbose: bool = False):
    """Set up logging to console."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate character asset images from a reference photo.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Full run with saree template:
  python run_character.py --name kamla --image ./kamla.jpg --template saree --skip goggles,headphone

  # Quick test with just one part (no GCS):
  python run_character.py --name kamla --image ./kamla.jpg --parts hair --local-only

  # Generate + submit to Meshy for 3D:
  python run_character.py --name kamla --image ./kamla.jpg --meshy hair,face
        """,
    )

    parser.add_argument(
        "--name", required=True,
        help="Character name (used for output folder names).",
    )

    # Exactly one of --image or --prompt is required.
    ref_group = parser.add_mutually_exclusive_group(required=True)
    ref_group.add_argument(
        "--image",
        help="Path to the reference image (person/character on white background).",
    )
    ref_group.add_argument(
        "--prompt",
        help="Text description of a character. Step 0 will generate a T-pose "
             "reference image from this prompt before running the pipeline. "
             "E.g. 'Indian woman in a red saree, age 30, medium brown skin'.",
    )
    parser.add_argument(
        "--template",
        help="Character template name (e.g. 'saree'). Uses default prompts if not set.",
    )
    parser.add_argument(
        "--skip",
        help="Comma-separated list of parts to SKIP (e.g. 'goggles,headphone').",
    )
    parser.add_argument(
        "--parts",
        help="Run ONLY these parts (comma-separated). For cheap testing. "
             "E.g. '--parts hair' runs only hair (plus fullbody for reference).",
    )
    parser.add_argument(
        "--meshy",
        help="Comma-separated parts to submit to Meshy for 3D model generation. "
             "E.g. '--meshy hair,face'. Requires MESHY_API_KEY in .env.",
    )
    parser.add_argument(
        "--provider", choices=["vertex", "gemini"],
        help="Image backend: 'vertex' (Vertex AI, default) or 'gemini' "
             "(Gemini Developer API, needs GEMINI_API_KEY). Overrides IMAGE_PROVIDER.",
    )
    parser.add_argument(
        "--local-only", action="store_true",
        help="Skip GCS upload. Save output only to local 'output/' folder.",
    )
    parser.add_argument(
        "--output-dir", default="output",
        help="Local output directory (default: 'output').",
    )
    parser.add_argument(
        "--config", default="prompts.yaml",
        help="Path to prompts config file (default: 'prompts.yaml').",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging.",
    )

    return parser.parse_args()


def main():
    args = parse_args()
    setup_logging(args.verbose)

    logger = logging.getLogger("run_character")

    # Parse comma-separated lists
    skip_parts = args.skip.split(",") if args.skip else None
    only_parts = args.parts.split(",") if args.parts else None
    meshy_parts = args.meshy.split(",") if args.meshy else None

    logger.info("=" * 60)
    logger.info("CHARACTER ASSET GENERATION PIPELINE")
    logger.info("=" * 60)
    logger.info("Character : %s", args.name)
    logger.info("Input     : %s", args.image or f'[prompt] "{args.prompt}"')
    logger.info("Template  : %s", args.template or "default")
    logger.info("Skip      : %s", skip_parts or "none")
    logger.info("Only parts: %s", only_parts or "all")
    logger.info("Meshy     : %s", meshy_parts or "none")
    logger.info("Provider  : %s", args.provider or "default (IMAGE_PROVIDER/vertex)")
    logger.info("Local only: %s", args.local_only)
    logger.info("=" * 60)

    # --- Step 0: generate reference image from prompt if needed ---
    reference_image_path = args.image
    if args.prompt:
        from gemini_client import generate_character_reference

        logger.info("Step 0: Generating T-pose reference image from prompt...")
        ref_image = generate_character_reference(
            description=args.prompt,
            provider=args.provider,
        )
        if ref_image is None:
            logger.error("Step 0 failed: could not generate reference image. "
                         "Try rephrasing the prompt or check your API key.")
            sys.exit(1)

        # Save the generated reference next to the output
        import os
        ref_dir = os.path.join(args.output_dir, args.name)
        os.makedirs(ref_dir, exist_ok=True)
        reference_image_path = os.path.join(ref_dir, "reference_generated.png")
        ref_image.save(reference_image_path, "PNG")
        logger.info("Step 0 complete. Reference saved: %s", reference_image_path)

    try:
        result = run_pipeline(
            character_name=args.name,
            reference_image_path=reference_image_path,
            template_name=args.template,
            skip_parts=skip_parts,
            only_parts=only_parts,
            meshy_parts=meshy_parts,
            local_only=args.local_only,
            config_path=args.config,
            output_dir=args.output_dir,
            provider=args.provider,
        )

        if "error" in result:
            logger.error("Pipeline failed: %s", result["error"])
            sys.exit(1)

        # Print summary
        logger.info("")
        logger.info("=" * 60)
        logger.info("PIPELINE COMPLETE")
        logger.info("=" * 60)
        logger.info("Parts generated: %s", ", ".join(result.get("parts_generated", [])))
        logger.info("Zip: %s", result.get("zip", "N/A"))

        if result.get("meshy"):
            logger.info("Meshy results:")
            for part, meshy_data in result["meshy"].items():
                logger.info("  %s: %s", part, meshy_data.get("model_urls", {}))

        # Also write result to a JSON file for reference
        result_path = f"{args.output_dir}/{args.name}/result.json"
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        logger.info("Full result saved to: %s", result_path)

    except FileNotFoundError as e:
        logger.error("File not found: %s", str(e))
        sys.exit(1)
    except Exception as e:
        logger.error("Pipeline crashed: %s", str(e), exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
