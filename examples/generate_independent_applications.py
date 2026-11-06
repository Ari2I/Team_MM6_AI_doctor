"""Создаёт синтетические заявки независимо от справочника и эталонных ответов."""

import argparse
from pathlib import Path
from random import Random

from openpyxl import Workbook

DIAGNOSES = (
    "K21.9 Гастроэзофагеальная рефлюксная болезнь", "N30.0 Острый цистит",
    "J30.1 Аллергический ринит", "L20.9 Атопический дерматит",
    "G43.9 Мигрень неуточнённая", "B35.1 Микоз ногтей",
    "M54.5 Боль в нижней части спины", "L50.9 Крапивница", "F32.0 Депрессивный эпизод",
)
PURCHASES = (
    "Омез", "Фурадонин", "Кларитин", "Экзодерил", "Нурофен", "Аквадетрим",
    "Амигренин", "Доларен", "Ко-Амлесса", "Флуоксетин", "Парацетамол",
    "Амоксициллин + клавулановая кислота",
)


def generate(directory: Path, seed: int, count: int) -> list[Path]:
    rng = Random(seed)
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for index in range(count):
        diagnoses = rng.sample(DIAGNOSES, 2)
        drugs = rng.sample(PURCHASES, 3) + [
            "Амоксициллин + клавулановая кислота",
            rng.choice(PURCHASES) + "я",
            f"Неизвестная позиция {rng.randrange(1000, 9999)}",
        ]
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Синтетические покупки"
        sheet.append(["Торговое наименование", "Количество", "Сумма", "Диагноз"])
        for row, drug in enumerate(drugs):
            sheet.append([drug, rng.randint(1, 3), rng.randint(85, 1700) if row != 4 else None,
                          "; ".join(diagnoses) if row == 0 else None])
        target = directory / f"application-{seed}-{index + 1:02}.xlsx"
        workbook.save(target)
        paths.append(target)
    return paths


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=20261106)
    parser.add_argument("--count", type=int, choices=range(1, 21), default=6, metavar="1..20")
    args = parser.parse_args()
    for file in generate(args.output, args.seed, args.count):
        print(file)
