"""Voice memos in.

Andrew records a note while driving and texts it. The audio is Andrew's own
voice, not third-party content, so it does not go through the quarantined
reader - it goes through transcription and then follows exactly the same path
as a typed message, gate included.

The media has to be fetched from Twilio with the account credentials and
copied into Andrew's own bucket before transcription, because a Twilio media
URL is a public-ish link that stays live until the message is deleted. Errand
deletes the Twilio copy once it has its own.
"""

from __future__ import annotations

import base64
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Protocol

from errand.common import clock, config, ids, secrets
from errand.reader.schemas import clean_text
from errand.store import audit_store

MAX_BYTES = 8 * 1024 * 1024
POLL_SECONDS = 2
DEFAULT_TIMEOUT = 45


class TranscriptionError(RuntimeError):
    """The memo could not be turned into text."""


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
            raise TranscriptionError("no transcript queued")
        return self.transcripts.pop(0)


class AmazonTranscribe:
    """Amazon Transcribe, batch mode.

    Batch rather than streaming because the audio already exists as a file by
    the time we see it; there is nothing to stream. The job is polled with a
    bounded wait so a stuck job fails the task instead of holding the SQS
    message until it redelivers.
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

        suffix = {"audio/mpeg": "mp3", "audio/mp4": "mp4", "audio/amr": "amr",
                  "audio/ogg": "ogg", "audio/wav": "wav"}.get(content_type, "mp3")
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


def fetch_twilio_media(url: str) -> bytes:
    """Twilio media needs the account credentials; the URL alone is not enough
    on an account configured to require them, and it should be."""
    creds = secrets.twilio_credentials()
    basic = base64.b64encode(f"{creds['account_sid']}:{creds['auth_token']}".encode()).decode()
    request = urllib.request.Request(url)
    request.add_header("Authorization", f"Basic {basic}")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise TranscriptionError(f"could not fetch the memo: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise TranscriptionError(f"could not fetch the memo: {exc.reason}") from exc


def delete_twilio_media(url: str) -> bool:
    """Once the audio is in Andrew's bucket, Twilio should not keep a copy."""
    creds = secrets.twilio_credentials()
    basic = base64.b64encode(f"{creds['account_sid']}:{creds['auth_token']}".encode()).decode()
    request = urllib.request.Request(url, method="DELETE")
    request.add_header("Authorization", f"Basic {basic}")
    try:
        urllib.request.urlopen(request, timeout=15).close()
        return True
    except (urllib.error.HTTPError, urllib.error.URLError):
        return False


def transcribe_memo(media: list[dict[str, str]], *, task_id: str = "system") -> str:
    """Turn the first audio attachment into text. Returns "" when there is none."""
    audio = [m for m in media if str(m.get("content_type", "")).startswith("audio/")]
    if not audio:
        return ""

    item = audio[0]
    audit_store.write(
        task_id=task_id,
        phase=audit_store.PHASE_BEFORE,
        event="VOICE_MEMO_RECEIVED",
        detail={"content_type": item.get("content_type", "")},
    )

    payload = fetch_twilio_media(item["url"])
    if len(payload) > MAX_BYTES:
        raise TranscriptionError("that memo is too long; text me instead")

    text = get_transcriber().transcribe(payload, item.get("content_type", "audio/mpeg"))
    delete_twilio_media(item["url"])

    cleaned = clean_text(text, 1200)
    audit_store.write(
        task_id=task_id,
        phase=audit_store.PHASE_AFTER,
        event="VOICE_MEMO_TRANSCRIBED",
        outcome="OK" if cleaned else "EMPTY",
        detail={"chars": len(cleaned)},
        sequence=1,
    )
    if not cleaned:
        raise TranscriptionError("I could not make out that memo")
    return cleaned
