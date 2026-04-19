from __future__ import annotations


def compare_highlight_model_keys(
    *,
    model_sigs: dict[str, str],
    round_no: int,
    round_matched_count: int,
) -> set[str]:
    if round_no != 1:
        return set()

    non_empty = {key: sig for key, sig in model_sigs.items() if sig}
    if len(non_empty) == len(model_sigs) and len(set(non_empty.values())) == 1:
        return set()

    if round_matched_count >= 2:
        freq: dict[str, int] = {}
        for sig in non_empty.values():
            freq[sig] = freq.get(sig, 0) + 1
        majority_sig = next((sig for sig, count in freq.items() if count >= 2), None)
        if majority_sig is None:
            return set(model_sigs.keys())
        return {key for key, sig in model_sigs.items() if not sig or sig != majority_sig}

    return set(model_sigs.keys())
