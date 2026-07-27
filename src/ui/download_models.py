import os
import sys

# Add parent directory to path for imports
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(os.path.dirname(current_dir))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from src import whisper


def download_model():
    models = whisper.available_models()
    cache_folder = os.path.join(os.path.expanduser('~'), f'.cache{os.path.sep}whisper{os.path.sep}')

    for model in models:
        print(f"Downloading {model}")
        whisper._download(url=whisper._MODELS[model], root=cache_folder, in_memory=False)


if __name__ == "__main__":
    download_model()
