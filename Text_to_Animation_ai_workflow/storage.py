"""
storage.py — Save images locally and to Google Cloud Storage.

GCS bucket: comfyui-assets-cf56be07 (public access for Meshy URLs + user downloads)
Layout: gs://comfyui-assets-cf56be07/characters/{character_name}/{part}_{view}.png

Also creates zip archives of all character assets.
"""

import io
import logging
import os
import zipfile

from google.cloud import storage as gcs
from PIL import Image

logger = logging.getLogger(__name__)

BUCKET_NAME = "comfyui-assets-cf56be07"
GCS_BASE_PATH = "characters"


def _get_bucket():
    """Get the GCS bucket object."""
    client = gcs.Client()
    return client.bucket(BUCKET_NAME)


def save_image_locally(image: Image.Image, local_path: str) -> str:
    """
    Save a PIL Image to a local file path.

    Args:
        image: PIL Image to save
        local_path: Full local file path (e.g. output/kamla/hair_front.png)

    Returns:
        The local_path that was written.
    """
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    image.save(local_path, "PNG")
    logger.info("Saved locally: %s", local_path)
    return local_path


def upload_to_gcs(local_path: str, gcs_path: str) -> str:
    """
    Upload a local file to GCS.

    Args:
        local_path: Path to the local file.
        gcs_path: Path within the bucket (e.g. characters/kamla/hair_front.png)

    Returns:
        Public URL of the uploaded file.
    """
    bucket = _get_bucket()
    blob = bucket.blob(gcs_path)
    blob.upload_from_filename(local_path)

    public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{gcs_path}"
    logger.info("Uploaded to GCS: %s", public_url)
    return public_url


def save_character_assets(
    character_name: str,
    processed_views: dict[str, dict[str, Image.Image]],
    local_output_dir: str = "output",
    upload_gcs: bool = True,
) -> dict[str, dict[str, str]]:
    """
    Save all processed view images locally and optionally to GCS.

    Args:
        character_name: e.g. "kamla"
        processed_views: Dict of {part_name: {view_name: PIL.Image}}
                         e.g. {"hair": {"front": <Image>, "left": <Image>, ...}}
        local_output_dir: Base local directory (default "output")
        upload_gcs: Whether to upload to GCS (False for --local-only mode)

    Returns:
        Dict of {part_name: {view_name: url_or_path}}
        URLs if uploaded to GCS, local paths otherwise.
    """
    char_dir = os.path.join(local_output_dir, character_name)
    os.makedirs(char_dir, exist_ok=True)

    result = {}

    for part_name, views in processed_views.items():
        result[part_name] = {}
        for view_name, image in views.items():
            filename = f"{part_name}_{view_name}.png"
            local_path = os.path.join(char_dir, filename)

            # Save locally
            save_image_locally(image, local_path)

            if upload_gcs:
                # Upload to GCS
                gcs_path = f"{GCS_BASE_PATH}/{character_name}/{filename}"
                url = upload_to_gcs(local_path, gcs_path)
                result[part_name][view_name] = url
            else:
                result[part_name][view_name] = os.path.abspath(local_path)

    logger.info(
        "Saved %d parts × 4 views for character '%s'",
        len(processed_views), character_name,
    )
    return result


def create_zip(
    character_name: str,
    local_output_dir: str = "output",
    upload_gcs: bool = True,
) -> str:
    """
    Zip all PNGs in the character's local output folder.

    Args:
        character_name: e.g. "kamla"
        local_output_dir: Base local directory
        upload_gcs: Whether to also upload zip to GCS

    Returns:
        Path to the zip file (local path or GCS URL).
    """
    char_dir = os.path.join(local_output_dir, character_name)
    zip_filename = f"{character_name}_assets.zip"
    zip_path = os.path.join(local_output_dir, zip_filename)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename in sorted(os.listdir(char_dir)):
            if filename.endswith(".png"):
                file_path = os.path.join(char_dir, filename)
                # Store inside zip without the full directory path
                zf.write(file_path, filename)
                logger.debug("Added to zip: %s", filename)

    logger.info("Created zip: %s", zip_path)

    if upload_gcs:
        gcs_path = f"{GCS_BASE_PATH}/{character_name}/{zip_filename}"
        url = upload_to_gcs(zip_path, gcs_path)
        return url

    return os.path.abspath(zip_path)
