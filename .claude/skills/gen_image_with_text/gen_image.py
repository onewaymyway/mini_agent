"""Image generation skill using the Agnes Image API (Agnes Image 2.5 Flash).

Usage:
    /gen_image_with_text "A beautiful sunset beach scene"
    /gen_image_with_text --prompt-file prompt.txt
"""

import os
import sys
from pathlib import Path
from typing import List, Optional, Union


from agnes_tools import AgnesImageClient


def gen_image(
    prompt: Optional[str] = None,
    prompt_file: Optional[str] = None,
    size: str = "1024x1024",
    ratio: Optional[str] = None,
    save_path: Optional[str] = None,
    response_format: str = "url",
) -> dict:
    """Generate an image from text description.

    Args:
        prompt: Text description of the image to generate. Either this or prompt_file must be provided.
        prompt_file: Path to a file containing the prompt text. Mutually exclusive with prompt.
        size: Image size, default "1024x1024". Also accepts the tiered values
            "1K"/"2K"/"3K"/"4K" (recommended, pair with `ratio`).
        ratio: Aspect ratio to pair with a tiered `size` (e.g. "16:9"). One of
            "1:1", "3:4", "4:3", "16:9", "9:16", "2:3", "3:2", "21:9".
        save_path: Optional path to save the generated image
        response_format: Response format, "url" or "b64_json"

    Returns:
        dict with success status and image_url or error
    """
    if not prompt and not prompt_file:
        return {
            "success": False,
            "error": "Either 'prompt' or 'prompt_file' must be provided.",
        }

    if prompt and prompt_file:
        return {
            "success": False,
            "error": "Cannot specify both 'prompt' and 'prompt_file'. Use one or the other.",
        }

    if prompt_file:
        file_path = Path(prompt_file)
        if not file_path.exists():
            return {
                "success": False,
                "error": f"Prompt file not found: {prompt_file}",
            }
        prompt = file_path.read_text(encoding="utf-8").strip()
        if not prompt:
            return {
                "success": False,
                "error": f"Prompt file is empty: {prompt_file}",
            }

    api_key = os.environ.get("AGNES_API_KEY")
    if not api_key:
        return {
            "success": False,
            "error": "AGNES_API_KEY environment variable not set. Please set it before generating images.",
        }

    client = AgnesImageClient(api_key=api_key)

    result = client.text_to_image(
        prompt=prompt,
        size=size,
        ratio=ratio,
        response_format=response_format,
        save_path=save_path,
    )

    if result.get("data"):
        if response_format == "url":
            return {
                "success": True,
                "image_url": result["data"][0]["url"],
                "save_path": save_path,
            }
        elif response_format == "b64_json":
            return {
                "success": True,
                "image_b64": result["data"][0]["b64_json"],
                "save_path": save_path,
            }

    return {
        "success": False,
        "error": result,
    }


def edit_image(
    image_path: Union[str, List[str]],
    prompt: str,
    size: str = "1024x1024",
    ratio: Optional[str] = None,
    save_path: Optional[str] = None,
    response_format: str = "url",
) -> dict:
    """Edit an existing image, or compose multiple reference images, based on
    a text description.

    Args:
        image_path: Path/URL to the image to edit (local file or URL), or a
            list of paths/URLs for multi-image composition workflows.
        prompt: Text description of how to modify/combine the image(s)
        size: Image size, default "1024x1024" (also accepts "1K"/"2K"/"3K"/"4K")
        ratio: Aspect ratio to pair with a tiered `size` (e.g. "16:9")
        save_path: Optional path to save the edited/composed image
        response_format: Response format, "url" or "b64_json"

    Returns:
        dict with success status and image_url or error
    """
    api_key = os.environ.get("AGNES_API_KEY")
    if not api_key:
        return {
            "success": False,
            "error": "AGNES_API_KEY environment variable not set. Please set it before generating images.",
        }

    client = AgnesImageClient(api_key=api_key)

    result = client.image_to_image(
        image=image_path,
        prompt=prompt,
        size=size,
        ratio=ratio,
        response_format=response_format,
        save_path=save_path,
    )

    if result.get("data"):
        if response_format == "url":
            return {
                "success": True,
                "image_url": result["data"][0]["url"],
                "save_path": save_path,
            }
        elif response_format == "b64_json":
            return {
                "success": True,
                "image_b64": result["data"][0]["b64_json"],
                "save_path": save_path,
            }

    return {
        "success": False,
        "error": result,
    }


def main():
    """CLI entry point for the skill."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate images using Agnes AI")
    parser.add_argument(
        "mode", choices=["gen", "edit"], help="Mode: gen (text-to-image) or edit (image editing / multi-image composition)"
    )
    parser.add_argument(
        "prompt", nargs="?", help="Text description of the image (mutually exclusive with --prompt-file)",
    )
    parser.add_argument(
        "--prompt-file",
        dest="prompt_file",
        help="Path to a file containing the prompt text (mutually exclusive with prompt argument)",
    )
    parser.add_argument(
        "--size", default="1024x1024",
        help="Image size (default: 1024x1024). Also accepts tiered values 1K/2K/3K/4K (pair with --ratio).",
    )
    parser.add_argument(
        "--ratio", default=None,
        help="Aspect ratio to pair with a tiered --size, e.g. 16:9. "
             "One of 1:1, 3:4, 4:3, 16:9, 9:16, 2:3, 3:2, 21:9.",
    )
    parser.add_argument("--save-path", help="Path to save the image")
    parser.add_argument(
        "--image-path", dest="image_path", action="append",
        help="Path to input image (for edit mode). Repeat this flag to pass "
             "multiple reference images for multi-image composition, "
             "e.g. --image-path a.png --image-path b.png",
    )
    parser.add_argument(
        "--format", dest="response_format", default="url", choices=["url", "b64_json"]
    )

    args = parser.parse_args()

    if args.mode == "gen":
        result = gen_image(
            prompt=args.prompt,
            prompt_file=args.prompt_file,
            size=args.size,
            ratio=args.ratio,
            save_path=args.save_path,
            response_format=args.response_format,
        )
    else:
        if not args.image_path:
            print("Error: --image-path is required for edit mode (repeat it for multi-image composition)")
            sys.exit(1)
        # argparse action="append" 收集成 list；单张图就传字符串，多张
        # 图（多图合成）原样传 list 给 edit_image -> AgnesImageClient。
        image_arg = args.image_path[0] if len(args.image_path) == 1 else args.image_path
        result = edit_image(
            image_path=image_arg,
            prompt=args.prompt,
            size=args.size,
            ratio=args.ratio,
            save_path=args.save_path,
            response_format=args.response_format,
        )

    if result["success"]:
        print("Image generated successfully!")
        if result.get("image_url"):
            print(f"Image URL: {result['image_url']}")
        if result.get("image_b64"):
            print(f"Base64 length: {len(result['image_b64'])}")
        if result.get("save_path"):
            print(f"Saved to: {result['save_path']}")
    else:
        print(f"Failed to generate image: {result.get('error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
