import argparse
import time

from rylanflow import __version__
from rylanflow.recorder import SAMPLE_RATE, Recorder, write_wav


def record(seconds: float, output: str) -> None:
    recorder = Recorder()
    print(f"Recording {seconds:g}s... speak now")
    recorder.start()
    time.sleep(seconds)
    audio = recorder.stop()
    write_wav(output, audio)
    peak = float(abs(audio).max()) if audio.size else 0.0
    print(f"Saved {audio.size / SAMPLE_RATE:.1f}s of audio to {output} (peak {peak:.2f})")


def main() -> None:
    parser = argparse.ArgumentParser(prog="rylanflow")
    parser.add_argument("--version", action="version", version=f"RylanFlow {__version__}")
    sub = parser.add_subparsers(dest="command")
    rec = sub.add_parser("record", help="record from the mic to a WAV file")
    rec.add_argument("-s", "--seconds", type=float, default=3.0)
    rec.add_argument("-o", "--output", default="recording.wav")
    args = parser.parse_args()

    if args.command == "record":
        record(args.seconds, args.output)
    else:
        print(f"RylanFlow {__version__}")


if __name__ == "__main__":
    main()
