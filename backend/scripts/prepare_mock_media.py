"""Create small, playable local fixtures without downloading remote media."""
from pathlib import Path
import shutil
import subprocess


def main() -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to prepare local mock media")
    root = Path(__file__).resolve().parents[1] / "static" / "mock" / "local"
    root.mkdir(parents=True, exist_ok=True)
    base = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    subprocess.run(base + ["-f", "lavfi", "-i", "testsrc2=size=854x480:rate=24",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100", "-t", "8",
        "-c:v", "libx264", "-preset", "fast", "-crf", "30", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", str(root / "default.mp4")], check=True)
    subprocess.run(base + ["-i", str(root / "default.mp4"), "-frames:v", "1", str(root / "default.jpg")], check=True)
    subprocess.run(base + ["-i", str(root / "default.mp4"), "-vn", "-c:a", "libmp3lame",
        "-b:a", "64k", str(root / "default.mp3")], check=True)
    print("Local mock video, image and audio prepared:", root)


if __name__ == "__main__":
    main()
