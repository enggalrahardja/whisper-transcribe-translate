import hashlib
import io
import os
import threading
import time
import urllib
import warnings
from contextlib import contextmanager
from typing import Callable, Iterator, List, Optional, Union

try:
    import fcntl
except ImportError:  # pragma: no cover - native Ubuntu uses fcntl
    fcntl = None

import torch
from tqdm import tqdm

from .audio import load_audio, log_mel_spectrogram, pad_or_trim
from .decoding import DecodingOptions, DecodingResult, decode, detect_language
from .model import ModelDimensions, Whisper
from .transcribe import transcribe
from .version import __version__

_MODELS = {
    "tiny.en": "https://openaipublic.azureedge.net/main/whisper/models/d3dd57d32accea0b295c96e26691aa14d8822fac7d9d27d5dc00b4ca2826dd03/tiny.en.pt",
    "tiny": "https://openaipublic.azureedge.net/main/whisper/models/65147644a518d12f04e32d6f3b26facc3f8dd46e5390956a9424a650c0ce22b9/tiny.pt",
    "base.en": "https://openaipublic.azureedge.net/main/whisper/models/25a8566e1d0c1e2231d1c762132cd20e0f96a85d16145c3a00adf5d1ac670ead/base.en.pt",
    "base": "https://openaipublic.azureedge.net/main/whisper/models/ed3a0b6b1c0edf879ad9b11b1af5a0e6ab5db9205f891f668f8b0e6c6326e34e/base.pt",
    "small.en": "https://openaipublic.azureedge.net/main/whisper/models/f953ad0fd29cacd07d5a9eda5624af0f6bcf2258be67c92b79389873d91e0872/small.en.pt",
    "small": "https://openaipublic.azureedge.net/main/whisper/models/9ecf779972d90ba49c06d968637d720dd632c55bbf19d441fb42bf17a411e794/small.pt",
    "medium.en": "https://openaipublic.azureedge.net/main/whisper/models/d7440d1dc186f76616474e0ff0b3b6b879abc9d1a4926b7adfa41db2d497ab4f/medium.en.pt",
    "medium": "https://openaipublic.azureedge.net/main/whisper/models/345ae4da62f9b3d59415adc60127b97c714f32e89e936602e85993674d08dcb1/medium.pt",
    "large-v1": "https://openaipublic.azureedge.net/main/whisper/models/e4b87e7e0bf463eb8e6956e646f1e277e901512310def2c24bf0e11bd3c28e9a/large-v1.pt",
    "large-v2": "https://openaipublic.azureedge.net/main/whisper/models/81f7c96c852ee8fc832187b0132e569d6c3065a3252ed18e56effd0b6a73e524/large-v2.pt",
    "large-v3": "https://openaipublic.azureedge.net/main/whisper/models/e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb/large-v3.pt",
    "large": "https://openaipublic.azureedge.net/main/whisper/models/e5b1a55b89c1367dacf97e3e19bfd829a01529dbfdeefa8caeb59b3f1b81dadb/large-v3.pt",
}

# base85-encoded (n_layers, n_heads) boolean arrays indicating the cross-attention heads that are
# highly correlated to the word-level timing, i.e. the alignment between audio and text tokens.
_ALIGNMENT_HEADS = {
    "tiny.en": b"ABzY8J1N>@0{>%R00Bk>$p{7v037`oCl~+#00",
    "tiny": b"ABzY8bu8Lr0{>%RKn9Fp%m@SkK7Kt=7ytkO",
    "base.en": b"ABzY8;40c<0{>%RzzG;p*o+Vo09|#PsxSZm00",
    "base": b"ABzY8KQ!870{>%RzyTQH3`Q^yNP!>##QT-<FaQ7m",
    "small.en": b"ABzY8>?_)10{>%RpeA61k&I|OI3I$65C{;;pbCHh0B{qLQ;+}v00",
    "small": b"ABzY8DmU6=0{>%Rpa?J`kvJ6qF(V^F86#Xh7JUGMK}P<N0000",
    "medium.en": b"ABzY8usPae0{>%R7<zz_OvQ{)4kMa0BMw6u5rT}kRKX;$NfYBv00*Hl@qhsU00",
    "medium": b"ABzY8B0Jh+0{>%R7}kK1fFL7w6%<-Pf*t^=N)Qr&0RR9",
    "large-v1": b"ABzY8r9j$a0{>%R7#4sLmoOs{s)o3~84-RPdcFk!JR<kSfC2yj",
    "large-v2": b"ABzY8zd+h!0{>%R7=D0pU<_bnWW*tkYAhobTNnu$jnkEkXqp)j;w1Tzk)UH3X%SZd&fFZ2fC2yj",
    "large-v3": b"ABzY8gWO1E0{>%R7(9S+Kn!D~%ngiGaR?*L!iJG9p-nab0JQ=-{D1-g00",
    "large": b"ABzY8gWO1E0{>%R7(9S+Kn!D~%ngiGaR?*L!iJG9p-nab0JQ=-{D1-g00",
}

_download_thread_locks: dict[str, threading.Lock] = {}
_download_thread_locks_guard = threading.Lock()


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _model_download_lock(
    target: str,
    cancel_callback: Optional[Callable[[], bool]],
) -> Iterator[None]:
    lock_path = f"{target}.lock"
    with _download_thread_locks_guard:
        thread_lock = _download_thread_locks.setdefault(lock_path, threading.Lock())

    while not thread_lock.acquire(timeout=0.2):
        if cancel_callback and cancel_callback():
            raise InterruptedError("Model download was interrupted while waiting for the model lock")

    lock_file = None
    try:
        if fcntl is not None:
            lock_file = open(lock_path, "a+b")
            while True:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if cancel_callback and cancel_callback():
                        raise InterruptedError("Model download was interrupted while waiting for the model lock")
                    time.sleep(0.2)
        yield
    finally:
        if lock_file is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
        thread_lock.release()


def _download(
    url: str,
    root: str,
    in_memory: bool,
    progress_callback: Optional[Callable[[int], None]] = None,
    download_start_callback: Optional[Callable[[], None]] = None,
    cancel_callback: Optional[Callable[[], bool]] = None,
) -> Union[bytes, str]:
    os.makedirs(root, exist_ok=True)

    expected_sha256 = url.split("/")[-2]
    download_target = os.path.join(root, os.path.basename(url))
    temporary_target = f"{download_target}.download"

    with _model_download_lock(download_target, cancel_callback):
        if os.path.exists(download_target) and not os.path.isfile(download_target):
            raise RuntimeError(f"{download_target} exists and is not a regular file")

        if os.path.isfile(download_target):
            if _sha256_file(download_target) == expected_sha256:
                if in_memory:
                    with open(download_target, "rb") as file:
                        return file.read()
                return download_target
            warnings.warn(
                f"{download_target} exists, but the SHA256 checksum does not match; re-downloading the file"
            )

        if cancel_callback and cancel_callback():
            raise InterruptedError("Model download was interrupted")
        if download_start_callback:
            download_start_callback()
        if progress_callback:
            progress_callback(0)

        try:
            with urllib.request.urlopen(url) as source, open(temporary_target, "wb") as output:
                content_length = source.info().get("Content-Length")
                total = int(content_length) if content_length else None
                downloaded = 0
                last_cancel_check = 0.0
                last_reported_percentage = 0
                with tqdm(
                    total=total,
                    ncols=80,
                    unit="iB",
                    unit_scale=True,
                    unit_divisor=1024,
                ) as loop:
                    while True:
                        current_time = time.monotonic()
                        if cancel_callback and current_time - last_cancel_check >= 0.25:
                            last_cancel_check = current_time
                            if cancel_callback():
                                raise InterruptedError("Model download was interrupted")
                        buffer = source.read(8192)
                        if not buffer:
                            break
                        output.write(buffer)
                        downloaded += len(buffer)
                        loop.update(len(buffer))
                        if progress_callback and total:
                            percentage = min(100, int(downloaded * 100 / total))
                            if percentage > last_reported_percentage:
                                last_reported_percentage = percentage
                                progress_callback(percentage)

            if _sha256_file(temporary_target) != expected_sha256:
                raise RuntimeError(
                    "Model has been downloaded but the SHA256 checksum does not match. Please retry loading the model."
                )
            os.replace(temporary_target, download_target)
            if progress_callback and last_reported_percentage < 100:
                progress_callback(100)
        except BaseException:
            try:
                os.remove(temporary_target)
            except FileNotFoundError:
                pass
            raise

        if in_memory:
            with open(download_target, "rb") as file:
                return file.read()
        return download_target


def available_models() -> List[str]:
    """Returns the names of available models"""
    return list(_MODELS.keys())


def load_model(
    name: str,
    device: Optional[Union[str, torch.device]] = None,
    download_root: str = None,
    in_memory: bool = False,
    download_progress_callback: Optional[Callable[[int], None]] = None,
    download_start_callback: Optional[Callable[[], None]] = None,
    cancel_callback: Optional[Callable[[], bool]] = None,
) -> Whisper:
    """
    Load a Whisper ASR model

    Parameters
    ----------
    name : str
        one of the official model names listed by `whisper.available_models()`, or
        path to a model checkpoint containing the model dimensions and the model state_dict.
    device : Union[str, torch.device]
        the PyTorch device to put the model into
    download_root: str
        path to download the model files; by default, it uses "~/.cache/whisper"
    in_memory: bool
        whether to preload the model weights into host memory

    Returns
    -------
    model : Whisper
        The Whisper ASR model instance
    """

    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
    if download_root is None:
        default = os.path.join(os.path.expanduser("~"), ".cache")
        download_root = os.path.join(os.getenv("XDG_CACHE_HOME", default), "whisper")

    if name in _MODELS:
        checkpoint_file = _download(
            _MODELS[name],
            download_root,
            in_memory,
            progress_callback=download_progress_callback,
            download_start_callback=download_start_callback,
            cancel_callback=cancel_callback,
        )
        alignment_heads = _ALIGNMENT_HEADS[name]
    elif os.path.isfile(name):
        checkpoint_file = open(name, "rb").read() if in_memory else name
        alignment_heads = None
    else:
        raise RuntimeError(
            f"Model {name} not found; available models = {available_models()}"
        )

    if cancel_callback and cancel_callback():
        raise InterruptedError("Model loading was interrupted")

    with (
        io.BytesIO(checkpoint_file) if in_memory else open(checkpoint_file, "rb")
    ) as fp:
        checkpoint = torch.load(fp, map_location=device)
    del checkpoint_file

    dims = ModelDimensions(**checkpoint["dims"])
    model = Whisper(dims)
    model.load_state_dict(checkpoint["model_state_dict"])

    if cancel_callback and cancel_callback():
        raise InterruptedError("Model loading was interrupted")

    if alignment_heads is not None:
        model.set_alignment_heads(alignment_heads)

    return model.to(device)
