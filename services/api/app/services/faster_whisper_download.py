"""Isolated faster-whisper download process so registry cancellation can terminate it."""

import sys
from pathlib import Path

from faster_whisper.utils import download_model


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: faster_whisper_download MODEL_ID OUTPUT_DIRECTORY")
    model_id, output_directory = sys.argv[1], Path(sys.argv[2])
    output_directory.mkdir(parents=True, exist_ok=True)
    download_model(model_id, output_dir=str(output_directory))


if __name__ == "__main__":
    main()
