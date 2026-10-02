import argparse
import time
import wave

import numpy as np

from rylanflow import __version__
from rylanflow.recorder import SAMPLE_RATE, Recorder, write_wav
from rylanflow.transcriber import DEFAULT_MODEL, MLXWhisperTranscriber


def record(seconds: float, output: str) -> None:
    recorder = Recorder()
    print(f"Recording {seconds:g}s... speak now")
    recorder.start()
    time.sleep(seconds)
    audio = recorder.stop()
    write_wav(output, audio)
    peak = float(abs(audio).max()) if audio.size else 0.0
    print(f"Saved {audio.size / SAMPLE_RATE:.1f}s of audio to {output} (peak {peak:.2f})")


def read_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as f:
        if f.getframerate() != SAMPLE_RATE or f.getnchannels() != 1:
            raise SystemExit(f"{path} must be {SAMPLE_RATE} Hz mono (use `rylanflow record`)")
        pcm = np.frombuffer(f.readframes(f.getnframes()), dtype=np.int16)
    return pcm.astype(np.float32) / 32768.0


def transcribe(path: str, model: str) -> None:
    print(f"Transcribing {path} with {model} (first run downloads the model)...")
    print(MLXWhisperTranscriber(model).transcribe(read_wav(path)))


def main() -> None:
    parser = argparse.ArgumentParser(prog="rylanflow")
    parser.add_argument("--version", action="version", version=f"RylanFlow {__version__}")
    sub = parser.add_subparsers(dest="command")
    rec = sub.add_parser("record", help="record from the mic to a WAV file")
    rec.add_argument("-s", "--seconds", type=float, default=3.0)
    rec.add_argument("-o", "--output", default="recording.wav")
    tr = sub.add_parser("transcribe", help="transcribe a WAV file to text")
    tr.add_argument("file")
    tr.add_argument("-m", "--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    if args.command == "record":
        record(args.seconds, args.output)
    elif args.command == "transcribe":
        transcribe(args.file, args.model)
    else:
        print(f"RylanFlow {__version__}")


if __name__ == "__main__":
    main()
