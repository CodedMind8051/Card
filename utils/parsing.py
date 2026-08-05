import re
import json


def extract_json(text):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def parse_bbox_ratios(bbox_data):
    if not bbox_data:
        return None
    if isinstance(bbox_data, list) and len(bbox_data) == 4:
        try:
            ratios = [float(v) for v in bbox_data]
            if all(0.0 <= r <= 1.0 for r in ratios):
                return ratios
        except (ValueError, TypeError):
            pass
    if isinstance(bbox_data, str):
        nums = re.findall(r'\d+\.\d+|\d+', bbox_data)
        if len(nums) >= 4:
            ratios = [float(n) for n in nums[:4]]
            if all(0.0 <= r <= 1.0 for r in ratios):
                return ratios
    return None