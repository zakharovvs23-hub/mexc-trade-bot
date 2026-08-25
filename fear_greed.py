import requests


def get_fear_greed():
    """
    Публичный индекс Fear & Greed (crypto-market-wide, не по конкретной монете).
    Источник: alternative.me
    """
    try:
        r = requests.get("https://api.alternative.me/fng/", timeout=10)
        r.raise_for_status()
        data = r.json()["data"][0]
        value = int(data["value"])
        classification = data["value_classification"]
        return value, classification
    except Exception:
        return None, None


def fear_greed_note(value, classification):
    if value is None:
        return "недоступен"
    if value >= 75:
        return f"{value} ({classification}) — экстремальная жадность, осторожнее с покупками"
    if value <= 25:
        return f"{value} ({classification}) — экстремальный страх, можно присмотреться к покупке при подтверждении"
    return f"{value} ({classification}) — нейтрально, не мешает и не помогает"
