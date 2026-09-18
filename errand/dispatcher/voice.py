"""Voice notes.

Andrew records a note while driving and sends it. The audio is his own voice,
not third-party content, so it does not go through the quarantined reader - it
goes through transcription and then follows exactly the same path as a typed
message, gate included.

Fetching the audio is the Channel's job, so this file does not know whether it
came from Telegram or, in v2, from a phone call.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol

from errand.common import clock, config, ids
from errand.reader.schemas import clean_text
from errand.store import audit_store

MAX_BYTES = 20 * 1024 * 1024
POLL_SECONDS = 2
DEFAULT_TIMEOUT = 45


class TranscriptionError(RuntimeError):
    """The note could not be turned into text."""


class Transcriber(Protocol):
    def transcribe(self, audio: bytes, content_type: str) -> str: ...


@dataclass
class FakeTranscriber:
    """Tests hand it the text they want back."""

    transcripts: list[str] = field(default_factory=list)
    calls: list[tuple[int, str]] = field(default_factory=list)

    def transcribe(self, audio: bytes, content_type: str) -> str:
        self.calls.append((len(audio), content_type))
        if not self.transcripts:
            raise TranscriptionError("I could not make out that note")
        return self.transcripts.pop(0)


class AmazonTranscribe:
    """Amazon Transcribe, batch mode.

    Batch rather than streaming because the audio already exists as a file by
    the time we see it. The job is polled with a bounded wait so a stuck job
    fails the task instead of holding the SQS message until it redelivers.
    """

    def __init__(self, region: str, bucket: str, timeout: int = DEFAULT_TIMEOUT) -> None:
        import boto3

        self._s3 = boto3.client("s3", region_name=region)
        self._transcribe = boto3.client("transcribe", region_name=region)
        self._bucket = bucket
        self._timeout = timeout

    def transcribe(self, audio: bytes, content_type: str) -> str:
        if not self._bucket:
            raise TranscriptionError("no transcription bucket configured")

        # Telegram voice notes are OGG/Opus; audio/* covers a forwarded file.
        suffix = {
            "audio/ogg": "ogg", "audio/opus": "ogg", "audio/mpeg": "mp3",
            "audio/mp4": "mp4", "audio/m4a": "mp4", "audio/wav": "wav",
        }.get(content_type, "ogg")
        job = f"errand-{ids.new_run_id()}"
        key = f"voice/{job}.{suffix}"

        self._s3.put_object(
            Bucket=self._bucket, Key=key, Body=audio, ContentType=content_type,
            ServerSideEncryption="aws:kms",
        )
        self._transcribe.start_transcription_job(
            TranscriptionJobName=job,
            Media={"MediaFileUri": f"s3://{self._bucket}/{key}"},
            MediaFormat=suffix,
            LanguageCode="en-US",
            OutputBucketName=self._bucket,
            OutputKey=f"voice/{job}.json",
        )

        deadline = clock.now() + self._timeout
        while clock.now() < deadline:
            status = self._transcribe.get_transcription_job(TranscriptionJobName=job)
            state = status["TranscriptionJob"]["TranscriptionJobStatus"]
            if state == "COMPLETED":
                return self._read_result(f"voice/{job}.json")
            if state == "FAILED":
                reason = status["TranscriptionJob"].get("FailureReason", "unknown")
                raise TranscriptionError(f"transcription failed: {reason}")
            time.sleep(POLL_SECONDS)
        raise TranscriptionError("transcription timed out")

    def _read_result(self, key: str) -> str:
        import json

        body = self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        payload = json.loads(body)
        results = payload.get("results", {}).get("transcripts", [])
        return " ".join(r.get("transcript", "") for r in results).strip()


_transcriber: Transcriber | None = None


def set_transcriber(transcriber: Transcriber | None) -> None:
    global _transcriber
    _transcriber = transcriber


def get_transcriber() -> Transcriber:
    global _transcriber
    if _transcriber is None:
        cfg = config.load()
        _transcriber = AmazonTranscribe(cfg.region, cfg.transcribe_bucket)
    return _transcriber


def transcribe_note(audio: bytes, content_type: str = "audio/ogg",
                    *, task_id: str = "system") -> str:
    """Turn a voice note into text. Raises TranscriptionError on anything that
    should be reported to Andrew rather than retried."""
    audit_store.write(
        task_id=task_id,
        phase=audit_store.PHASE_BEFORE,
        event="VOICE_NOTE_RECEIVED",
        detail={"content_type": content_type, "bytes": len(audio)},
    )

    if len(audio) > MAX_BYTES:
        raise TranscriptionError("that note is too long; type it instead")

    text = get_transcriber().transcribe(audio, content_type)
    cleaned = clean_text(text, 1200)

    audit_store.write(
        task_id=task_id,
        phase=audit_store.PHASE_AFTER,
        event="VOICE_NOTE_TRANSCRIBED",
        outcome="OK" if cleaned else "EMPTY",
        detail={"chars": len(cleaned)},
        sequence=1,
    )
    if not cleaned:
        raise TranscriptionError("I could not make out that note")
    return cleaned
