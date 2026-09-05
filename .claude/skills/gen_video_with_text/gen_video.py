"""Video generation skill using Agnes Video 2.5 Flash API.

Usage:
    python gen_video.py text "prompt" --save-path output.mp4
    python gen_video.py keyframe "prompt" --first-frame first.png --last-frame last.png --save-path output.mp4
    python gen_video.py reference "prompt" --images a.png b.png --save-path output.mp4
"""

import os
import sys
from pathlib import Path
from typing import Optional, List


from agnes_tools import AgnesVideoClient


def gen_video(
    prompt: Optional[str] = None,
    prompt_file: Optional[str] = None,
    mode: str = "text",
    seconds: str = "5",
    size: str = "720P",
    aspect_ratio: str = "16:9",
    first_frame: Optional[str] = None,
    last_frame: Optional[str] = None,
    images: Optional[List[str]] = None,
    audios: Optional[List[str]] = None,
    seed: Optional[int] = None,
    save_path: Optional[str] = None,
) -> dict:
    """Generate a video using Agnes Video 2.5 Flash.

    Args:
        prompt: Text description of the video. Either this or prompt_file must be provided.
        prompt_file: Path to a file containing the prompt text. Mutually exclusive with prompt.
        mode: "text", "keyframe" or "reference".
        seconds: Video duration as a string, "4"-"12", default "5".
        size: Video resolution. Flash only supports "720P".
        aspect_ratio: One of 21:9, 16:9, 4:3, 1:1, 3:4, 9:16. Default "16:9".
        first_frame: First frame image URL (keyframe mode).
        last_frame: Last frame image URL (keyframe mode).
        images: List of reference image URLs (reference mode, max 5 for Flash).
        audios: List of reference audio URLs (reference mode, max 3 for Flash).
        seed: Optional random seed.
        save_path: Optional path to save the generated video.

    Returns:
        dict with success status and video_url or error
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

    if size != "720P":
        return {
            "success": False,
            "error": "agnes-video-2.5-flash only supports size='720P'.",
        }

    if mode == "keyframe" and not (first_frame or last_frame):
        return {
            "success": False,
            "error": "keyframe mode requires at least one of 'first_frame' or 'last_frame'.",
        }

    if mode == "reference":
        if not (images or audios):
            return {
                "success": False,
                "error": "reference mode requires at least one of 'images' or 'audios'.",
            }
        if images and len(images) > 5:
            return {
                "success": False,
                "error": "images length must not exceed 5 for agnes-video-2.5-flash.",
            }
        if audios and len(audios) > 3:
            return {
                "success": False,
                "error": "audios length must not exceed 3 for agnes-video-2.5-flash.",
            }

    api_key = os.environ.get("AGNES_API_KEY")
    if not api_key:
        return {
            "success": False,
            "error": "AGNES_API_KEY environment variable not set. Please set it before generating videos.",
        }

    client = AgnesVideoClient(api_key=api_key)

    result = client.generate_video(
        prompt=prompt,
        mode=mode,
        seconds=seconds,
        size=size,
        aspect_ratio=aspect_ratio,
        first_frame=first_frame,
        last_frame=last_frame,
        images=images,
        audios=audios,
        seed=seed,
        save_path=save_path,
    )

    return result


def main():
    """CLI entry point for the skill."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate videos using Agnes Video 2.5 Flash")
    parser.add_argument(
        "mode",
        choices=["text", "keyframe", "reference"],
        help="Generation mode: text (text-to-video), keyframe (first/last frame control), reference (image/audio reference)",
    )
    parser.add_argument(
        "prompt", nargs="?", help="Text description of the video (mutually exclusive with --prompt-file)",
    )
    parser.add_argument(
        "--prompt-file",
        dest="prompt_file",
        help="Path to a file containing the prompt text (mutually exclusive with prompt argument)",
    )
    parser.add_argument("--seconds", default="5", help="Video duration, '4'-'12' (default: 5)")
    parser.add_argument("--size", default="720P", help="Video resolution (Flash only supports 720P)")
    parser.add_argument("--aspect-ratio", dest="aspect_ratio", default="16:9", help="Aspect ratio (default: 16:9)")
    parser.add_argument("--first-frame", dest="first_frame", help="First frame image URL (keyframe mode)")
    parser.add_argument("--last-frame", dest="last_frame", help="Last frame image URL (keyframe mode)")
    parser.add_argument("--images", nargs="*", help="Reference image URLs (reference mode, max 5)")
    parser.add_argument("--audios", nargs="*", help="Reference audio URLs (reference mode, max 3)")
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument("--save-path", help="Path to save the generated video")

    args = parser.parse_args()

    result = gen_video(
        prompt=args.prompt,
        prompt_file=args.prompt_file,
        mode=args.mode,
        seconds=args.seconds,
        size=args.size,
        aspect_ratio=args.aspect_ratio,
        first_frame=args.first_frame,
        last_frame=args.last_frame,
        images=args.images,
        audios=args.audios,
        seed=args.seed,
        save_path=args.save_path,
    )

    if result.get("success"):
        print("Video generated successfully!")
        if result.get("video_url"):
            print(f"Video URL: {result['video_url']}")
        if result.get("save_path"):
            print(f"Saved to: {result['save_path']}")
    else:
        print(f"Failed to generate video: {result.get('error')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
