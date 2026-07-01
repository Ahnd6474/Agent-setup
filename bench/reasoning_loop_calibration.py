#!/usr/bin/env python3
"""Measure where Qwen reasoning first enters a repeated sentence/paragraph loop."""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterator

MODEL = "mlx-community/Qwen3.6-35B-A3B-4bit"
LEVELS = ("minimal", "low", "medium", "high", "xhigh")
BOUNDARY = re.compile(r"(?:\r?\n)+|(?<=[.!?。！？])\s+")
EDGE_CHARS = " \t\r\n\"'`*_#>-–—()[]{}“”‘’"

CASES: dict[str, list[tuple[str, str]]] = {
    "minimal": [
        ("identity", "너 안성민이야? 한 문장으로 답해."),
        ("arithmetic", "17+28의 값과 계산 한 줄만 말해."),
        ("translation", "'The server is ready.'를 자연스러운 한국어 한 문장으로 번역해."),
    ],
    "low": [
        ("linear-equation", "3x+7=25를 풀고 검산해."),
        ("probability", "공정한 동전을 세 번 던져 정확히 두 번 앞면이 나올 확률을 구해."),
        ("code-review", "Python 코드 `items=[]; print(items[0])`의 오류 원인과 최소 수정안을 설명해."),
    ],
    "medium": [
        (
            "logic",
            "A는 B보다 크고 C는 A보다 작다. B와 C의 대소관계가 항상 정해지는지 "
            "반례 또는 증명으로 판단해.",
        ),
        (
            "rate",
            "수조를 A관은 6시간, B관은 4시간에 채우고 배수관은 12시간에 비운다. "
            "세 관을 동시에 열면 가득 차는 시간을 구해.",
        ),
        (
            "algorithm",
            "정렬된 정수 배열에서 합이 target인 두 수의 인덱스를 찾는 O(n) 알고리즘을 "
            "설명하고 Python으로 작성해.",
        ),
    ],
    "high": [
        (
            "invariant",
            "8x8 체스판에서 마주 보는 두 모서리를 제거했다. 1x2 도미노 31개로 "
            "남은 칸을 덮을 수 없는 이유를 불변량으로 증명해.",
        ),
        (
            "concurrency",
            "멀티스레드 작업 큐에서 작업 완료 직후 프로세스가 종료될 때 가끔 결과가 "
            "유실된다. enqueue, worker, ack, shutdown 순서를 포함해 가능한 경쟁 조건과 "
            "검증 가능한 수정안을 설계해.",
        ),
        (
            "constraints",
            "A,B,C,D 네 작업의 소요시간은 3,2,4,1이고 선행조건은 A→C, B→C, "
            "C→D다. 작업자 2명일 때 최소 완료시간과 일정을 증명해.",
        ),
    ],
    "xhigh": [
        (
            "number-theory",
            "모든 양의 정수 n에 대해 n^5-n이 30의 배수임을 서로 다른 두 방법으로 "
            "증명하고 각 방법의 핵심 불변 구조를 비교해.",
        ),
        (
            "distributed-debug",
            "4노드 파이프라인 추론에서 리더 선거가 5초마다 반복되고 모델 runner가 "
            "Shutdown된다. 네트워크는 완전 연결처럼 보인다. 로그 가설, 최소 관측 지표, "
            "격리 실험, 롤백 기준을 순서대로 설계해.",
        ),
        (
            "optimization",
            "서로 다른 10개 작업을 3대의 동일한 서버에 배치한다. 작업시간이 "
            "[9,8,7,6,5,4,3,2,2,1]일 때 makespan을 최소화하는 배치와 최적성 하한을 "
            "제시해.",
        ),
    ],
}


@dataclass
class LoopMatch:
    onset_char: int
    previous_char: int
    similarity: float
    previous: str
    repeated: str


class LoopDetector:
    """Find the first near-duplicate completed reasoning unit."""

    def __init__(self, similarity: float = 0.93) -> None:
        self.similarity = similarity
        self.tail = ""
        self.reasoning_chars = 0
        self.segments: list[tuple[int, str, str]] = []

    @staticmethod
    def normalize(text: str) -> str:
        return " ".join(text.strip(EDGE_CHARS).lower().split())

    def feed(self, delta: str) -> LoopMatch | None:
        start = self.reasoning_chars - len(self.tail)
        self.reasoning_chars += len(delta)
        parts = BOUNDARY.split(self.tail + delta)
        self.tail = parts.pop() if parts else ""
        cursor = start
        for raw in parts:
            normalized = self.normalize(raw)
            onset = cursor
            cursor += len(raw) + 1
            if len(normalized) < 32 or len(normalized.split()) < 6:
                continue
            for previous_char, previous_raw, previous_norm in self.segments:
                length_ratio = min(len(normalized), len(previous_norm)) / max(
                    len(normalized), len(previous_norm)
                )
                if length_ratio < 0.75:
                    continue
                score = SequenceMatcher(None, previous_norm, normalized).ratio()
                if score >= self.similarity:
                    return LoopMatch(
                        onset_char=max(0, onset),
                        previous_char=previous_char,
                        similarity=round(score, 4),
                        previous=previous_raw[:500],
                        repeated=raw[:500],
                    )
            self.segments.append((onset, raw, normalized))
        return None


def stream_completion(
    base_url: str,
    *,
    model: str,
    effort: str,
    prompt: str,
    max_tokens: int,
    seed: int,
    timeout: float,
) -> Iterator[dict[str, str]]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
        "max_tokens": max_tokens,
        "reasoning_effort": effort,
        "enable_thinking": True,
        "seed": seed,
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        for encoded_line in response:
            line = encoded_line.decode(errors="replace").strip()
            if not line.startswith("data: "):
                continue
            raw = line[6:]
            if raw == "[DONE]":
                return
            event = json.loads(raw)
            choices = event.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            reasoning = delta.get("reasoning_content")
            if reasoning:
                yield {"type": "reasoning", "delta": reasoning}
            content = delta.get("content")
            if content:
                yield {"type": "content", "delta": content}


def run_case(
    args: argparse.Namespace,
    *,
    level: str,
    case_id: str,
    prompt: str,
    seed: int,
) -> dict[str, object]:
    detector = LoopDetector(args.similarity)
    reasoning: list[str] = []
    content: list[str] = []
    match: LoopMatch | None = None
    error: str | None = None
    started = time.monotonic()
    try:
        for event in stream_completion(
            args.base_url,
            model=args.model,
            effort=level,
            prompt=prompt,
            max_tokens=args.max_tokens,
            seed=seed,
            timeout=args.timeout,
        ):
            if event["type"] == "reasoning":
                reasoning.append(event["delta"])
                match = detector.feed(event["delta"])
                if match is not None:
                    break
            else:
                content.append(event["delta"])
    except (OSError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        error = str(exc)
    elapsed = round(time.monotonic() - started, 3)
    reasoning_text = "".join(reasoning)
    content_text = "".join(content)
    return {
        "level": level,
        "case_id": case_id,
        "seed": seed,
        "prompt": prompt,
        "elapsed_s": elapsed,
        "reasoning_chars": len(reasoning_text),
        "content_chars": len(content_text),
        "loop": asdict(match) if match else None,
        "completed": bool(content_text),
        "error": error,
        "reasoning": reasoning_text,
        "content": content_text,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:52415/v1")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--levels", nargs="+", choices=LEVELS, default=list(LEVELS))
    parser.add_argument("--cases-per-level", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260630)
    parser.add_argument("--max-tokens", type=int, default=8192)
    parser.add_argument("--similarity", type=float, default=0.93)
    parser.add_argument("--timeout", type=float, default=900.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.home()
        / ".agentic-local"
        / "benchmarks"
        / "reasoning-loops"
        / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as output:
        for level in args.levels:
            for case_id, prompt in CASES[level][: args.cases_per_level]:
                for repeat in range(args.repeats):
                    result = run_case(
                        args,
                        level=level,
                        case_id=case_id,
                        prompt=prompt,
                        seed=args.seed + repeat,
                    )
                    output.write(json.dumps(result, ensure_ascii=False) + "\n")
                    output.flush()
                    loop = result["loop"]
                    state = (
                        f"loop@{loop['onset_char']}"  # type: ignore[index]
                        if isinstance(loop, dict)
                        else "completed"
                        if result["completed"]
                        else "no-answer"
                    )
                    print(
                        f"{level:7} {case_id:18} {state:14} "
                        f"reasoning={result['reasoning_chars']:6} "
                        f"elapsed={result['elapsed_s']:7}s"
                    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
