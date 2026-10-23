import json
import re
from pathlib import Path
from typing import Any

TIMESTAMP_RE = re.compile(r"(?P<h>\d{2}):(?P<m>\d{2}):(?P<s>\d{2})(?:[.,](?P<ms>\d{1,3}))?")

def timestamp_to_seconds(value: str) -> float:
    match = TIMESTAMP_RE.search(value)
    if not match:
        return 0.0
    return int(match["h"]) * 3600 + int(match["m"]) * 60 + int(match["s"]) + float("0." + (match["ms"] or "0"))

def seconds_to_timestamp(seconds: float) -> str:
    total = max(0, int(seconds))
    return f"{total//3600:02d}:{(total%3600)//60:02d}:{total%60:02d}"

def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()

def parse_vtt(path: Path) -> list[dict[str, Any]]:
    content = path.read_text(encoding="utf-8-sig")
    blocks = re.split(r"\n\s*\n", content.strip())
    segments = []
    for block in blocks:
        lines = [x.strip() for x in block.splitlines() if x.strip()]
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        start = timestamp_to_seconds(lines[timing_index].split("-->")[0])
        body = " ".join(lines[timing_index+1:])
        # VTT speaker markup may cover one or multiple lines; capture each speaker block.
        matches = re.findall(r"<v\s+([^>]+)>(.*?)</v>", body, flags=re.S)
        if matches:
            for speaker, text in matches:
                clean = _clean_text(text)
                if clean:
                    segments.append({"start_seconds": start, "timestamp": seconds_to_timestamp(start),
                                     "speaker_raw": speaker.strip(), "text": clean})
        else:
            clean = _clean_text(body)
            if clean:
                segments.append({"start_seconds": start, "timestamp": seconds_to_timestamp(start),
                                 "speaker_raw": "", "text": clean})
    return segments

def parse_srt(path: Path) -> list[dict[str, Any]]:
    content = path.read_text(encoding="utf-8-sig")
    blocks = re.split(r"\n\s*\n", content.strip())
    segments = []
    for block in blocks:
        lines = [x.strip() for x in block.splitlines() if x.strip()]
        timing_index = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_index is None:
            continue
        start = timestamp_to_seconds(lines[timing_index].split("-->")[0])
        body = " ".join(lines[timing_index+1:])
        # Expected form in this task: SPEAKER: spoken text.
        match = re.match(r"([^:]{1,80}):\s*(.*)", body)
        speaker, text = (match.group(1).strip(), match.group(2).strip()) if match else ("", body)
        if text:
            segments.append({"start_seconds": start, "timestamp": seconds_to_timestamp(start),
                             "speaker_raw": speaker, "text": _clean_text(text)})
    return segments

def parse_txt(path: Path) -> list[dict[str, Any]]:
    content = path.read_text(encoding="utf-8-sig")
    segments = []
    pattern = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s*([^:]+):\s*(.*)$")
    for line in content.splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        stamp, speaker, text = match.groups()
        if text.strip():
            seconds = timestamp_to_seconds(stamp)
            segments.append({"start_seconds": seconds, "timestamp": stamp,
                             "speaker_raw": speaker.strip(), "text": _clean_text(text)})
    return segments

def parse_json(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    segments = []
    for item in payload.get("segments", []):
        start = float(item.get("start_ms", 0)) / 1000
        segments.append({"start_seconds": start, "timestamp": seconds_to_timestamp(start),
                         "speaker_raw": str(item.get("spk", "")),
                         "text": _clean_text(str(item.get("content", "")))})
    return [x for x in segments if x["text"]]

def parse_transcript(path: Path, fmt: str | None = None) -> list[dict[str, Any]]:
    fmt = (fmt or path.suffix.lstrip(".")).lower()
    parsers = {"vtt": parse_vtt, "srt": parse_srt, "txt": parse_txt, "json": parse_json}
    if fmt not in parsers:
        raise ValueError(f"Unsupported transcript format: {fmt}")
    return parsers[fmt](path)
