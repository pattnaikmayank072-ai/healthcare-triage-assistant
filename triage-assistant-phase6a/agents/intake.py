"""Intake Agent: structured facts must be directly supported by source quotes."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from schemas import FactField, FollowUpPlan, FollowUpQuestion, QuoteFact, SymptomRecord
from schemas import LanguageResult
from agents.safety import SafetyAgent
from config import settings

ModelCall = Callable[[dict[str, Any]], Any]


class IntakeAgent:
    """Schema-checked intake extraction with one repair attempt, then deterministic fallback.

    A provider is never contacted here. If supplied by the caller, ``model_call`` must
    already be routed through the central LLM Gateway and receive redacted English only.
    """

    def __init__(self, model_call: ModelCall | None = None,
                 safety_agent: SafetyAgent | None = None):
        self.model_call = model_call
        self.safety = safety_agent or SafetyAgent()

    def run(self, redacted_english_text: str, scenario: str = "general_outpatient") -> SymptomRecord:
        source = redacted_english_text.strip()
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        if not source:
            return SymptomRecord(facts=[], status="insufficient_information", scenario=scenario,
                                 source_text_sha256=digest)
        if self.model_call is not None:
            try:
                egress_text = self.safety.prepare_external_text(source)
            except Exception:
                egress_text = None
            if egress_text:
                egress_digest = hashlib.sha256(egress_text.encode("utf-8")).hexdigest()
            else:
                egress_digest = ""
            for repair_attempt in range(2):
                if not egress_text:
                    break
                try:
                    payload = self.model_call({
                        "text": egress_text,
                        "scenario": scenario,
                        "repair_attempt": repair_attempt,
                        "schema": "SymptomRecord",
                    })
                    record = self._validate_model_payload(payload, egress_text, scenario, egress_digest)
                    return record
                except Exception:
                    # One schema/response repair retry only; no patient content in logs.
                    continue
        return self._fallback(source, scenario, digest)

    @staticmethod
    def _validate_model_payload(payload: Any, source: str, scenario: str, digest: str) -> SymptomRecord:
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ValueError("model output must be an object")
        facts_raw = payload.get("facts", [])
        if not isinstance(facts_raw, list):
            raise ValueError("facts must be a list")
        supported: list[QuoteFact] = []
        for item in facts_raw:
            fact = QuoteFact.model_validate(item)
            if fact.quote in source:
                supported.append(fact)
        status = "structured" if supported else "insufficient_information"
        # Server-owned metadata is not accepted from a model response.
        return SymptomRecord(facts=supported, status=status, scenario=scenario,
                             source_text_sha256=digest)

    @staticmethod
    def _fallback(source: str, scenario: str, digest: str) -> SymptomRecord:
        """Extract only a small set of verbatim, high-confidence surface facts."""
        facts: list[QuoteFact] = []
        candidates: list[tuple[FactField, re.Pattern[str], Callable[[re.Match[str]], str]]] = [
            ("age", re.compile(r"\b(?:I am|age[d]?|aged)\s+(\d{1,3})\s*(?:years?|yrs?)?\b", re.I),
             lambda m: m.group(1)),
            ("duration", re.compile(r"\b(?:for|since)\s+((?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:hours?|days?|weeks?|months?))\b", re.I),
             lambda m: m.group(1)),
            ("pregnancy", re.compile(r"\b(I am pregnant|pregnant|not pregnant)\b", re.I),
             lambda m: m.group(1)),
            ("severity", re.compile(r"\b(mild|moderate|severe)\b", re.I), lambda m: m.group(1)),
        ]
        for field, pattern, get_value in candidates:
            match = pattern.search(source)
            if match:
                quote = match.group(0)
                facts.append(QuoteFact(field=field, value=get_value(match), quote=quote))
        complaint = re.search(r"\b(?:I have|I feel|complaint is|problem is)\s+([^.!?\n]{1,120})", source, re.I)
        if complaint:
            quote = complaint.group(0)
            facts.append(QuoteFact(field="complaint", value=complaint.group(1).strip(), quote=quote))
        # Never invent onset, sex, conditions, medicines, or timeline facts.
        return SymptomRecord(facts=facts, status="insufficient_information",
                             scenario=scenario, source_text_sha256=digest)


class LanguageVoiceAgent:
    """Local-first transcription, language detection and translation boundary.

    ``local_transcriber`` and ``local_translator`` are intended for faster-whisper and
    IndicTrans2 adapters. Optional Sarvam callbacks are invoked only when Sarvam is
    configured and external use is enabled; audio egress additionally requires the
    synthetic-audio flag. Original transcript text is encrypted before it is returned.
    """

    def __init__(self,
                 local_transcriber: Callable[[bytes, str | None], Any] | None = None,
                 local_translator: Callable[[str, str, str], str] | None = None,
                 sarvam_translator: Callable[[str, str, str], str] | None = None,
                 sarvam_transcriber: Callable[[bytes, str | None], Any] | None = None,
                 original_encryptor: Callable[[str], str] | None = None,
                 safety_agent: SafetyAgent | None = None,
                 sarvam_enabled: bool | None = None,
                 synthetic_audio_allowed: bool | None = None):
        self.local_transcriber = local_transcriber
        self.local_translator = local_translator
        self.sarvam_translator = sarvam_translator
        self.sarvam_transcriber = sarvam_transcriber
        self.original_encryptor = original_encryptor
        self.safety = safety_agent or SafetyAgent()
        self.sarvam_enabled = settings.provider_enabled("sarvam") if sarvam_enabled is None else sarvam_enabled
        self.synthetic_audio_allowed = (settings.synthetic_audio_allowed if synthetic_audio_allowed is None
                                        else synthetic_audio_allowed)

    @staticmethod
    def detect_language(text: str) -> str:
        devanagari = sum("\u0900" <= ch <= "\u097f" for ch in text)
        latin = sum(ch.isascii() and ch.isalpha() for ch in text)
        if devanagari and latin:
            return "hi-en"
        if devanagari:
            return "hi"
        if latin:
            return "en"
        return "und"

    def _encrypt_original(self, text: str) -> str | None:
        try:
            if self.original_encryptor is not None:
                return self.original_encryptor(text)
            from store import encrypt_sensitive_text
            return encrypt_sensitive_text(text)
        except Exception:
            return None

    def process_text(self, original_text: str, language_hint: str | None = None) -> LanguageResult:
        text = original_text.strip()
        language = (language_hint or self.detect_language(text)).lower()
        if not text:
            return LanguageResult(status="needs_interpreter", detected_language=language,
                                  needs_interpreter=True, flags=["empty_input"])
        encrypted = self._encrypt_original(text)
        if encrypted is None:
            return LanguageResult(status="quarantined", detected_language=language,
                                  needs_interpreter=True, flags=["original_encryption_unavailable"])
        scanned = self.safety.scan_input(text)
        if not scanned.safe:
            return LanguageResult(status="quarantined", detected_language=language,
                                  encrypted_original_text=encrypted, needs_interpreter=True,
                                  flags=["injection_sentinel_quarantine"])
        if language.startswith("en"):
            return LanguageResult(status="complete", detected_language=language,
                                  english_text=text, encrypted_original_text=encrypted,
                                  needs_interpreter=False)

        if self.local_translator is not None:
            try:
                translated = self.local_translator(text, language, "en")
                if isinstance(translated, str) and translated.strip():
                    return LanguageResult(status="complete", detected_language=language,
                                          english_text=translated.strip(), encrypted_original_text=encrypted,
                                          needs_interpreter=False, flags=["translated_locally"])
            except Exception:
                pass

        if self.sarvam_enabled and self.sarvam_translator is not None:
            try:
                # Only redacted, sentinel-cleared text may cross the external boundary.
                safe_text = self.safety.prepare_external_text(text)
                translated = self.sarvam_translator(safe_text, language, "en")
                if isinstance(translated, str) and translated.strip():
                    return LanguageResult(status="complete", detected_language=language,
                                          english_text=translated.strip(), encrypted_original_text=encrypted,
                                          needs_interpreter=False, flags=["sarvam_fallback"])
            except Exception:
                pass
        return LanguageResult(status="needs_interpreter", detected_language=language,
                              encrypted_original_text=encrypted, needs_interpreter=True,
                              flags=["translation_unavailable"])

    def process_audio(self, audio_bytes: bytes, language_hint: str | None = None,
                      synthetic_audio: bool = False) -> LanguageResult:
        if not audio_bytes or len(audio_bytes) > 25 * 1024 * 1024:
            return LanguageResult(status="needs_interpreter", detected_language=language_hint or "und",
                                  needs_interpreter=True, flags=["audio_missing_or_over_limit"])
        transcript: Any = None
        if self.local_transcriber is not None:
            try:
                transcript = self.local_transcriber(audio_bytes, language_hint)
            except Exception:
                transcript = None
        if transcript is None and self.sarvam_enabled and self.sarvam_transcriber is not None:
            if synthetic_audio and self.synthetic_audio_allowed:
                try:
                    transcript = self.sarvam_transcriber(audio_bytes, language_hint)
                except Exception:
                    transcript = None
        if isinstance(transcript, Mapping):
            transcript_text = transcript.get("text")
            detected = transcript.get("language") or language_hint
        else:
            transcript_text = transcript
            detected = language_hint
        if not isinstance(transcript_text, str) or not transcript_text.strip():
            return LanguageResult(status="needs_interpreter", detected_language=detected or "und",
                                  needs_interpreter=True, flags=["transcription_unavailable", "audio_not_persisted"])
        result = self.process_text(transcript_text, detected)
        if self.sarvam_transcriber is not None and result.status == "complete" and self.local_transcriber is None:
            result.flags.append("synthetic_audio_fallback")
        return result


class FollowUpPlanner:
    """Compare structured intake to explicit scenario fields; never invent requirements."""

    _templates: dict[str, str] = {
        "complaint": "What is the main complaint to record?",
        "onset": "When did it start?",
        "duration": "How long has this been happening?",
        "severity": "How severe is it, in the patient's own words?",
        "age": "What is the patient's age or age band?",
        "sex": "What sex should be recorded for this case?",
        "pregnancy": "Is pregnancy relevant to this case?",
        "condition": "Which relevant existing conditions should be recorded?",
        "medicine": "Which medicines has the patient mentioned?",
        "timeline": "What sequence and timing of symptoms should be recorded?",
    }

    def __init__(self, model_call: Callable[[dict[str, Any]], Any] | None = None,
                 safety_agent: SafetyAgent | None = None):
        self.model_call = model_call
        self.safety = safety_agent or SafetyAgent()

    def _model_questions(self, missing: list[str], scenario: str,
                         triage_category: str) -> dict[str, str]:
        if self.model_call is None or not settings.provider_enabled("gemini"):
            return {}
        # Only field identifiers and non-patient routing metadata cross this boundary.
        try:
            payload = self.model_call({
                "scenario": scenario,
                "triage_category": triage_category,
                "missing_fields": missing,
                "constraints": "Ask only for these fields; no diagnosis, advice, or additional fields.",
            })
            if isinstance(payload, str):
                payload = json.loads(payload)
            raw_questions = payload.get("questions", []) if isinstance(payload, Mapping) else payload
            if not isinstance(raw_questions, list):
                return {}
            result: dict[str, str] = {}
            for item in raw_questions:
                if isinstance(item, Mapping):
                    field = item.get("field")
                    question = item.get("question")
                else:
                    continue
                if field not in missing or not isinstance(question, str) or not question.strip():
                    continue
                if (not question.strip().endswith("?")
                        or not self.safety.scan_input(question.strip()).safe
                        or self.safety.guard_output(question.strip()).status != "cleared"):
                    continue
                result.setdefault(field, question.strip()[:500])
            return result
        except Exception:
            return {}

    def plan(self, record: SymptomRecord, required_fields: Sequence[str], *,
             scenario: str = "outpatient_queue",
             triage_category: str = "insufficient_information") -> FollowUpPlan:
        if any(not isinstance(field, str) for field in required_fields) or len(required_fields) > 100:
            return FollowUpPlan(
                status="needs_configuration", missing_information=[], suggested_questions=[],
                flags=["invalid_required_field_configuration"],
            )
        configured = list(dict.fromkeys(required_fields))
        if not configured:
            return FollowUpPlan(
                status="needs_configuration",
                missing_information=["scenario_required_fields_not_configured"],
                suggested_questions=[], priority_source="scenario_rulepack_order",
                flags=["required_fields_not_configured"],
            )
        unknown = [field for field in configured if field not in self._templates]
        if unknown:
            return FollowUpPlan(
                status="needs_configuration", missing_information=unknown,
                suggested_questions=[], priority_source="scenario_rulepack_order",
                flags=["unrecognized_required_fields"],
            )
        present = {fact.field for fact in record.facts if fact.value.strip()}
        missing = [field for field in configured if field not in present]
        if not missing:
            return FollowUpPlan(status="ready", missing_information=[], suggested_questions=[],
                                priority_source="scenario_rulepack_order", flags=[])

        model_questions = self._model_questions(missing, scenario, triage_category)
        questions = [
            FollowUpQuestion(
                field=field,
                question=model_questions.get(field, self._templates[field]),
                priority=position,
                source="model" if field in model_questions else "template",
            )
            for position, field in enumerate(missing[:5], start=1)
        ]
        return FollowUpPlan(
            status="ready", missing_information=missing,
            suggested_questions=questions,
            priority_source="scenario_rulepack_order",
            flags=["human_review_required"],
        )
