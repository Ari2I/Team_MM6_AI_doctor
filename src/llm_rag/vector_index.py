"""Небольшой векторный индекс в памяти с косинусной близостью."""

import math


def _unit_vector(vector: list[float], dimension: int) -> tuple[float, ...]:
    if not isinstance(vector, (list, tuple)) or len(vector) != dimension or not dimension:
        raise ValueError("Некорректная размерность вектора")
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
        raise ValueError("Вектор должен содержать только конечные числа")
    scale = max(abs(value) for value in vector)
    if scale == 0:
        raise ValueError("Нулевой вектор не задаёт направление для косинусной близости")
    # Масштабирование защищает норму от переполнения для больших конечных значений.
    scaled = [value / scale for value in vector]
    norm = math.sqrt(math.fsum(value * value for value in scaled))
    return tuple(value / norm for value in scaled)


class VectorIndex:
    """Хранение нормализованных векторов со стабильными идентификаторами фрагментов."""

    def __init__(self, fragment_ids: list[str], vectors: list[list[float]]):
        if not vectors or len(vectors) != len(fragment_ids) or len(set(fragment_ids)) != len(fragment_ids):
            raise ValueError("Идентификаторы и векторы должны быть непустыми и взаимно однозначными")
        self.dimension = len(vectors[0])
        self._vectors = {identifier: _unit_vector(vector, self.dimension)
                         for identifier, vector in zip(fragment_ids, vectors, strict=True)}

    def similarities(self, query: list[float]) -> dict[str, float]:
        unit = _unit_vector(query, self.dimension)
        return {identifier: max(-1.0, min(1.0, math.fsum(a*b for a, b in zip(unit, vector, strict=True))))
                for identifier, vector in self._vectors.items()}
