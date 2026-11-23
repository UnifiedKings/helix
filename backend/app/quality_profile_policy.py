"""Pure, deterministic quality profile validation and selection (no IO).

Quality levels are preferences, not proof of audio provenance or fidelity.
The existing downloader must continue to validate candidates independently.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

LOSSLESS = frozenset({'flac', 'alac', 'wav', 'aiff', 'aif'})
LOSSY = frozenset({'mp3', 'opus', 'aac', 'm4a', 'vorbis', 'ogg'})
CODECS = LOSSLESS | LOSSY
DecisionKind = Literal['reject', 'acceptable', 'upgrade', 'target_satisfied']


@dataclass(frozen=True)
class Decision:
    kind: DecisionKind
    reason: str
    current_tier: int | None = None
    candidate_tier: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return dict(kind=self.kind, reason=self.reason, current_tier=self.current_tier, candidate_tier=self.candidate_tier)


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _codec(value: Any) -> str:
    return str(value or '').strip().lower().lstrip('.')


def normalize_profile(data: dict[str, Any]) -> dict[str, Any]:
    """Validate user-authored policy. tiers are ordered best to worst.

    Every tier defines a set of allowed codecs and *minimum* attributes.
    Global ceilings are enforced independently of tier rankings.
    """
    if not isinstance(data, dict):
        raise ValueError('Profile must be an object')
    tiers = data.get('tiers')
    if not isinstance(tiers, list) or not 1 <= len(tiers) <= 20:
        raise ValueError('Profile requires 1–20 ordered quality tiers')
    cleaned = []
    for index, raw in enumerate(tiers):
        if not isinstance(raw, dict):
            raise ValueError(f'Tier {index + 1} must be an object')
        codecs = raw.get('codecs')
        if not isinstance(codecs, list) or not codecs or any(_codec(c) not in CODECS for c in codecs):
            raise ValueError(f'Tier {index + 1} must specify supported codecs')
        codecs = list(dict.fromkeys(_codec(c) for c in codecs))
        if any(c in LOSSLESS for c in codecs) and any(c in LOSSY for c in codecs):
            raise ValueError(f'Tier {index + 1} cannot mix lossy and lossless codecs')
        fields = ('min_bitrate', 'min_sample_rate', 'min_bit_depth')
        if any(isinstance(raw.get(f), bool) or not str(raw.get(f, 0)).isdigit() for f in fields):
            raise ValueError(f'Tier {index + 1} has an invalid numeric constraint')
        cleaned.append({
            'label': str(raw.get('label') or f'Tier {index + 1}').strip()[:100],
            'codecs': codecs,
            **{f: _int(raw.get(f)) for f in fields},
        })
    if any(isinstance(data.get(f), bool) or not str(data.get(f, 0)).isdigit() for f in ('max_bitrate', 'max_sample_rate', 'max_bit_depth')):
        raise ValueError('Invalid quality ceiling')
    result = {
        'tiers': cleaned,
        'target_tier': data.get('target_tier', 0),
        'max_bitrate': _int(data.get('max_bitrate')),
        'max_sample_rate': _int(data.get('max_sample_rate')),
        'max_bit_depth': _int(data.get('max_bit_depth')),
        'allow_intermediate': data.get('allow_intermediate', False),
        'stop_at_target': data.get('stop_at_target', True),
        'replace_lossless': data.get('replace_lossless', False),  # Backward-compatible legacy field (ignored by evaluator).
    }
    target = result['target_tier']
    if isinstance(target, bool) or not isinstance(target, int) or not 0 <= target < len(cleaned):
        raise ValueError('Target tier is outside the quality progression')
    for key in ('allow_intermediate', 'stop_at_target', 'replace_lossless'):
        if not isinstance(result[key], bool):
            raise ValueError(f'{key} must be a boolean')
    for tier in cleaned:
        for name, ceiling in (('min_bitrate', result['max_bitrate']), ('min_sample_rate', result['max_sample_rate']), ('min_bit_depth', result['max_bit_depth'])):
            if ceiling and tier[name] > ceiling:
                raise ValueError(f'{tier["label"]}: {name} exceeds configured maximum')
    return result


def tier_for(info: dict[str, Any], profile: dict[str, Any]) -> int | None:
    """First matching tier (best to worst); unknown codec or violated ceilings reject."""
    codec = _codec(info.get('codec'))
    if codec not in CODECS:
        return None
    for label, field in (('max_bitrate', 'bitrate'), ('max_sample_rate', 'sample_rate'), ('max_bit_depth', 'bit_depth')):
        if label == 'max_bitrate' and codec in LOSSLESS:
            continue  # Lossless bitrate varies with audio content, not a quality tier.
        limit = profile.get(label, 0)
        if limit and _int(info.get(field)) > limit:
            return None
    for index, tier in enumerate(profile['tiers']):
        if codec not in tier['codecs']:
            continue
        if any(tier[field] and _int(info.get(field.removeprefix('min_'))) < tier[field]
               for field in ('min_bitrate', 'min_sample_rate', 'min_bit_depth')):
            continue
        return index
    return None


def evaluate(current: dict[str, Any] | None, candidate: dict[str, Any], raw_profile: dict[str, Any], *, invalid_current: bool = False) -> Decision:
    """Classify a candidate, without reading files or modifying anything.

    Reject means never accept; acceptable means a valid permitted candidate
    not eligible for a normal quality upgrade; upgrade permits replacement;
    target_satisfied means the existing file has reached the configured cutoff.
    Bad-file bypasses *ranking*, never codec/quality ceilings or validation.
    """
    profile = normalize_profile(raw_profile)
    candidate_tier = tier_for(candidate, profile)
    current_tier = tier_for(current, profile) if current else None
    if candidate_tier is None:
        return Decision('reject', 'Candidate does not satisfy any permitted tier or exceeds a quality limit', current_tier, None)
    if invalid_current:
        return Decision('upgrade', 'Invalid existing file: acceptable replacement bypasses ranking', current_tier, candidate_tier)
    if not current or not _codec(current.get('codec')):
        return Decision('acceptable', 'No existing quality information for comparison', None, candidate_tier)
    if current_tier is None:
        return Decision('upgrade', 'Current file does not meet profile requirements', None, candidate_tier)
    if profile['stop_at_target'] and current_tier <= profile['target_tier']:
        return Decision('target_satisfied', 'Current file meets the configured target', current_tier, candidate_tier)
    if candidate_tier > current_tier:
        return Decision('acceptable', 'Candidate ranks below the existing file', current_tier, candidate_tier)
    if candidate_tier == current_tier:
        existing_codec = _codec(current.get('codec'))
        proposed_codec = _codec(candidate.get('codec'))
        if existing_codec != proposed_codec:
            return Decision('acceptable', 'Same-tier codec preference is not ordered', current_tier, candidate_tier)
        if existing_codec in LOSSLESS:
            # Never call a bit-depth improvement an upgrade if sample rate drops
            # (or vice versa). Lossless bitrate is unrelated to fidelity.
            fields = ('sample_rate', 'bit_depth')
        else:
            fields = ('bitrate',)
        existing = [_int(current.get(f)) for f in fields]
        proposed = [_int(candidate.get(f)) for f in fields]
        if not all(a and b for a, b in zip(existing, proposed)):
            return Decision('acceptable', 'Insufficient metadata to compare quality within tier', current_tier, candidate_tier)
        if not (all(b >= a for a, b in zip(existing, proposed)) and any(b > a for a, b in zip(existing, proposed))):
            return Decision('acceptable', 'Candidate is not a measurable improvement within tier', current_tier, candidate_tier)
    if not profile['allow_intermediate'] and candidate_tier > profile['target_tier']:
        return Decision('acceptable', 'Intermediate upgrades are disabled', current_tier, candidate_tier)
    return Decision('upgrade', 'Candidate improves quality or progression rank', current_tier, candidate_tier)


def legacy_profile(settings: dict[str, Any]) -> dict[str, Any]:
    """Snapshot current legacy settings; runtime worker stays on these settings in Phase 1.

    Legacy min sample rate/depth apply to all acceptable candidate formats.
    Lossy-only mode has broad tier preferences without assuming bitrate parity.
    """
    min_sr = _int(settings.get('quality_upgrade_min_sample_rate', 44100))
    min_depth = _int(settings.get('quality_upgrade_min_bit_depth', 16))
    lossless_tier = {'label': 'Lossless', 'codecs': ['flac', 'alac', 'wav', 'aiff', 'aif'], 'min_sample_rate': min_sr, 'min_bit_depth': min_depth}
    tiers = [lossless_tier]
    if not settings.get('quality_upgrade_lossless_only', True):
        tiers.append({'label': 'Lossy', 'codecs': ['mp3', 'opus', 'aac', 'm4a', 'vorbis', 'ogg'], 'min_sample_rate': min_sr, 'min_bit_depth': min_depth})
    return normalize_profile({
        'tiers': tiers, 'target_tier': 0, 'allow_intermediate': False,
        'stop_at_target': not bool(settings.get('quality_upgrade_replace_lossless', False)),
        'replace_lossless': bool(settings.get('quality_upgrade_replace_lossless', False)),
    })
