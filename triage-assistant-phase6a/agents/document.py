"""Document and Vision Agent: local OCR only; source pages are never sent externally."""
from __future__ import annotations

import io
import re
from collections.abc import Callable, Sequence
from typing import Any

from schemas import DocumentResult, RawLabRow

OCRAdapter = Callable[[bytes, str], str]


class DocumentAgent:
    """Read sanitized image-page bytes through local PaddleOCR then local Tesseract.

    This phase accepts already-sanitized pages only. The upload sandbox, file-signature
    checks, rendering, size limits, and isolation belong to later backend integration.
    """

    def __init__(self, adapters: Sequence[tuple[str, OCRAdapter]] | None = None):
        self.adapters = list(adapters) if adapters is not None else self._local_adapters()

    def run(self, sanitized_pages: Sequence[bytes]) -> DocumentResult:
        pages = list(sanitized_pages)
        if not pages or len(pages) > 100 or any(not isinstance(page, bytes) or not page for page in pages):
            return DocumentResult(status="unreadable_needs_manual_entry", page_count=0,
                                  note="Unreadable, needs manual entry.")
        attempted: list[str] = []
        page_texts: list[str] = []
        for page in pages:
            recognized = ""
            for name, adapter in self.adapters:
                attempted.append(name)
                try:
                    recognized = adapter(page, "eng+hin").strip()
                except Exception:
                    recognized = ""
                if recognized:
                    break
            if not recognized:
                return DocumentResult(status="unreadable_needs_manual_entry", page_count=len(pages),
                                      engines_attempted=list(dict.fromkeys(attempted)),
                                      note="Unreadable, needs manual entry.")
            page_texts.append(recognized)
        text = "\n".join(page_texts)
        rows = self.extract_rows(text)
        return DocumentResult(status="read", text=text, rows=rows, page_count=len(pages),
                              engines_attempted=list(dict.fromkeys(attempted)),
                              note="OCR text is unverified; reviewer confirmation required.")

    @staticmethod
    def extract_rows(text: str) -> list[RawLabRow]:
        """Parse only explicit delimited rows; do not infer units or reference ranges."""
        rows: list[RawLabRow] = []
        for line in text.splitlines():
            parts = [part.strip() for part in re.split(r"\s*\|\s*", line.strip())]
            if len(parts) < 2 or not parts[0] or not parts[1]:
                continue
            # Supported layout: name | value | unit? | reference range?
            name, value = parts[0], parts[1]
            unit = parts[2] or None if len(parts) > 2 else None
            reference = parts[3] or None if len(parts) > 3 else None
            rows.append(RawLabRow(name=name[:200], value=value[:100], unit=unit,
                                  reference_range=reference, source_quote=line.strip()[:1000]))
        return rows

    @staticmethod
    def _local_adapters() -> list[tuple[str, OCRAdapter]]:
        return [("paddleocr_local", _paddleocr_local), ("tesseract_local", _tesseract_local)]


def _image_array(page: bytes) -> Any:
    from PIL import Image
    import numpy as np
    return np.asarray(Image.open(io.BytesIO(page)).convert("RGB"))


def _paddleocr_local(page: bytes, languages: str) -> str:
    # Lazy import: absence is a normal, explicit fallback condition.
    from paddleocr import PaddleOCR
    engine = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
    result = engine.ocr(_image_array(page), cls=True)
    chunks: list[str] = []
    for block in result or []:
        for item in block or []:
            if len(item) > 1 and item[1]:
                chunks.append(str(item[1][0]))
    return "\n".join(chunks)


def _tesseract_local(page: bytes, languages: str) -> str:
    from PIL import Image
    import pytesseract
    image = Image.open(io.BytesIO(page)).convert("RGB")
    try:
        return pytesseract.image_to_string(image, lang="eng+hin")
    except Exception:
        return pytesseract.image_to_string(image, lang="eng")


# Phase 3: approved rule-pack data only; no clinical thresholds are embedded in code.
from dataclasses import dataclass
from math import isfinite
from collections.abc import Mapping

from schemas import LabFinding


@dataclass(frozen=True)
class LabRangeSpec:
    low: float | None
    high: float | None
    unit: str
    age_min: float | None = None
    age_max: float | None = None
    sex: str | None = None
    pregnancy: bool | None = None
    critical_low: float | None = None
    critical_high: float | None = None
    source: str = "approved_rule_pack"
    section: str = ""

    def __post_init__(self):
        if not self.unit.strip():
            raise ValueError("lab reference range unit must be explicit")
        values = (self.low, self.high, self.age_min, self.age_max,
                  self.critical_low, self.critical_high)
        if any(value is not None and not isfinite(value) for value in values):
            raise ValueError("lab ranges must use finite numeric values")
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError("lab reference low must not exceed high")


@dataclass(frozen=True)
class LabDefinition:
    name: str
    aliases: tuple[str, ...]
    unit: str
    ranges: tuple[LabRangeSpec, ...]
    unit_conversions: Mapping[str, float] | None = None


class VitalsLabAnalyst:
    """Normalize only configured tests/units and compare against explicit approved ranges."""

    def __init__(self, definitions: Sequence[LabDefinition] = ()):
        self._definitions: dict[str, LabDefinition] = {}
        for definition in definitions:
            if not definition.name.strip() or not definition.unit.strip():
                raise ValueError("lab definition requires a name and canonical unit")
            if any(not isfinite(float(factor)) or float(factor) <= 0
                   for factor in (definition.unit_conversions or {}).values()):
                raise ValueError("unit conversion factors must be finite and positive")
            for label in (definition.name, *definition.aliases):
                normalized = self._normalize_name(label)
                if normalized in self._definitions and self._definitions[normalized] != definition:
                    raise ValueError("lab name/alias collision in rule pack")
                self._definitions[normalized] = definition

    @classmethod
    def from_rule_pack(cls, path: str, scenario: str) -> "VitalsLabAnalyst":
        """Load only explicit thresholds from the selected scenario rule pack."""
        from pathlib import Path
        import yaml

        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        scenario_data = data.get("scenarios", {}).get(scenario, {})
        entries = scenario_data.get("lab_reference_ranges", []) if isinstance(scenario_data, dict) else []
        definitions: list[LabDefinition] = []

        def optional_float(value: Any) -> float | None:
            return None if value is None else float(value)

        if not isinstance(entries, list):
            raise ValueError("lab_reference_ranges must be a list")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("lab range entry must be an object")
            name = str(entry.get("name", "")).strip()
            unit = str(entry.get("unit", "")).strip()
            ranges_raw = entry.get("ranges", [])
            aliases_raw = entry.get("aliases", [])
            if not name or not unit or not isinstance(ranges_raw, list) or not isinstance(aliases_raw, list):
                raise ValueError("lab entry requires a name, unit and ranges list")
            ranges: list[LabRangeSpec] = []
            for raw_range in ranges_raw:
                if not isinstance(raw_range, dict) or not str(raw_range.get("unit", unit)).strip():
                    raise ValueError("invalid lab reference range")
                ranges.append(LabRangeSpec(
                    low=optional_float(raw_range.get("low")),
                    high=optional_float(raw_range.get("high")),
                    unit=str(raw_range.get("unit", unit)).strip(),
                    age_min=optional_float(raw_range.get("age_min")),
                    age_max=optional_float(raw_range.get("age_max")),
                    sex=str(raw_range["sex"]) if raw_range.get("sex") is not None else None,
                    pregnancy=raw_range.get("pregnancy"),
                    critical_low=optional_float(raw_range.get("critical_low")),
                    critical_high=optional_float(raw_range.get("critical_high")),
                    source=str(raw_range.get("source", "approved_rule_pack")),
                    section=str(raw_range.get("section", "")),
                ))
            conversions_raw = entry.get("unit_conversions", {})
            if not isinstance(conversions_raw, dict):
                raise ValueError("unit_conversions must be a mapping")
            conversions = {str(key): float(value) for key, value in conversions_raw.items()}
            definitions.append(LabDefinition(
                name=name,
                aliases=tuple(str(alias) for alias in aliases_raw),
                unit=unit,
                ranges=tuple(ranges),
                unit_conversions=conversions,
            ))
        return cls(definitions)

    @staticmethod
    def _normalize_name(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", value.casefold())

    @staticmethod
    def _parse_number(value: str) -> float | None:
        match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*", value)
        if not match:
            return None
        try:
            result = float(match.group(1))
            return result if isfinite(result) else None
        except (ValueError, OverflowError):
            return None

    @staticmethod
    def _range_matches(spec: LabRangeSpec, age: float | None,
                       sex: str | None, pregnancy: bool | None) -> bool:
        if spec.age_min is not None and (age is None or age < spec.age_min):
            return False
        if spec.age_max is not None and (age is None or age > spec.age_max):
            return False
        if spec.sex is not None and (sex is None or sex.casefold() != spec.sex.casefold()):
            return False
        if spec.pregnancy is not None and (pregnancy is None or pregnancy != spec.pregnancy):
            return False
        return True

    def analyze(self, rows: Sequence[RawLabRow], *, age: float | None = None,
                sex: str | None = None, pregnancy: bool | None = None) -> list[LabFinding]:
        findings: list[LabFinding] = []
        for row in rows:
            definition = self._definitions.get(self._normalize_name(row.name))
            if definition is None:
                findings.append(LabFinding(
                    test_name=row.name, observed_value=row.value, unit=row.unit,
                    status="unrecognized", source_quote=row.source_quote,
                    note="unrecognized, reviewer to check",
                ))
                continue

            parsed = self._parse_number(row.value)
            source_unit = (row.unit or "").strip()
            canonical_unit = definition.unit.strip()
            conversions = {k.strip().casefold(): float(v)
                           for k, v in (definition.unit_conversions or {}).items()}
            if source_unit.casefold() == canonical_unit.casefold():
                factor = 1.0
            else:
                factor = conversions.get(source_unit.casefold())
            if parsed is None or factor is None or not isfinite(factor) or factor <= 0:
                findings.append(LabFinding(
                    test_name=definition.name, observed_value=row.value,
                    normalized_value=None, unit=source_unit or None,
                    status="review", source_quote=row.source_quote,
                    note="Value or unit could not be normalized from the approved rule pack; reviewer to check.",
                ))
                continue

            normalized = parsed * factor
            matching = [spec for spec in definition.ranges
                        if self._range_matches(spec, age, sex, pregnancy)]
            if not matching:
                findings.append(LabFinding(
                    test_name=definition.name, observed_value=row.value,
                    normalized_value=normalized, unit=canonical_unit or None,
                    status="review", source_quote=row.source_quote,
                    note="No configured reference range matches the supplied demographics; reviewer to check.",
                ))
                continue
            specificity = lambda spec: sum(x is not None for x in
                                           (spec.age_min, spec.age_max, spec.sex, spec.pregnancy))
            most_specific = max(specificity(spec) for spec in matching)
            selected = [spec for spec in matching if specificity(spec) == most_specific]
            if len(selected) != 1:
                findings.append(LabFinding(
                    test_name=definition.name, observed_value=row.value,
                    normalized_value=normalized, unit=canonical_unit or None,
                    status="review", source_quote=row.source_quote,
                    note="Multiple configured reference ranges match; reviewer to check.",
                ))
                continue
            spec = selected[0]
            if spec.unit.casefold() != canonical_unit.casefold():
                findings.append(LabFinding(
                    test_name=definition.name, observed_value=row.value,
                    normalized_value=normalized, unit=canonical_unit or None,
                    status="review", source_quote=row.source_quote,
                    note="Reference range unit differs from normalized unit; reviewer to check.",
                ))
                continue
            if spec.low is None and spec.high is None:
                findings.append(LabFinding(
                    test_name=definition.name, observed_value=row.value,
                    normalized_value=normalized, unit=canonical_unit or None,
                    status="review", source_quote=row.source_quote,
                    note="Configured range has no numeric bounds; reviewer to check.",
                ))
                continue
            range_text = f"{spec.low if spec.low is not None else '—'} to {spec.high if spec.high is not None else '—'} {canonical_unit}".strip()
            if spec.critical_low is not None and normalized <= spec.critical_low:
                result_status = "critical_low"
            elif spec.critical_high is not None and normalized >= spec.critical_high:
                result_status = "critical_high"
            elif spec.low is not None and normalized < spec.low:
                result_status = "low"
            elif spec.high is not None and normalized > spec.high:
                result_status = "high"
            else:
                result_status = "normal"
            note = "Deterministic comparison against configured rule-pack range; reviewer confirmation required."
            findings.append(LabFinding(
                test_name=definition.name, observed_value=row.value,
                normalized_value=normalized, unit=canonical_unit or None,
                reference_range=range_text, status=result_status,
                source_quote=row.source_quote, note=note,
                reference_source=spec.source, reference_section=spec.section,
            ))
        return findings
