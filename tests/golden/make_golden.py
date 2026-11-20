"""Generate the synthetic golden set for eval_extraction (all data is made up).

Run: python tests/golden/make_golden.py
"""

import json
from pathlib import Path


def ex(make, model, year, mileage, executed, comment, recs_text, expected):
    return {
        "input": {
            "make": make, "model": model, "year": year, "mileage": mileage,
            "executed_works": executed, "mechanic_comment": comment,
            "recommendations_text": recs_text,
        },
        "expected": [
            {"work_type_code": code, "kind": kind, "due_km_delta": km, "due_months_delta": mo}
            for code, kind, km, mo in expected
        ],
    }  # fmt: skip


EXAMPLES = [
    ex("Lada", "Vesta", 2019, 45000, ["Замена масла ДВС"], "",
       "Колодки пер. 30%, менять через 5-7 т.км", [("brake_pads_front", "future", 5000, None)]),
    ex("Kia", "Rio", 2017, 98000, ["Диагностика ходовой части"],
       "Стук в передней подвеске — стойки стабилизатора. Клиент отказался от замены, приедет позже.", "",
       [("stabilizer_links", "declined", None, None)]),
    ex("Toyota", "Camry", 2018, 120000, ["Замена масла ДВС", "Замена воздушного фильтра"], "",
       "Рекомендуется замена ремня ГРМ и помпы через 10 тыс. км; тормозная жидкость — замена, потемнела.",
       [("timing_belt", "future", 10000, None), ("water_pump", "future", 10000, None),
        ("brake_fluid", "future", None, None)]),
    ex("Hyundai", "Solaris", 2016, 150000, ["Замена передних тормозных колодок"],
       "Передние колодки заменены. Задние колодки 2 мм — срочная замена, клиент отказался.", "",
       [("brake_pads_rear", "declined", 0, None)]),
    ex("Renault", "Logan", 2014, 210000, ["Замена масла ДВС"], "Клиент Тестов Иван Петрович, тел. +7 900 000-00-01. "
       "Течь масла из-под клапанной крышки, рекомендована замена прокладки.", "",
       [("valve_cover_gasket", "future", None, None)]),
    ex("Volkswagen", "Polo", 2020, 60000, ["Плановое ТО-4"], "Замечаний нет.", "", []),
    ex("Skoda", "Octavia", 2015, 175000, ["Замена масла ДВС"], "",
       "Через 6 месяцев — замена антифриза. Шаровые опоры люфт, замена в ближайшее время.",
       [("coolant", "future", None, 6), ("ball_joint", "future", 0, None)]),
    ex("Lada", "Granta", 2021, 30000, ["Шиномонтаж R15"], "", "", []),
    ex("Nissan", "Qashqai", 2017, 110000, ["Замена масла в вариаторе"],
       "Масло в вариаторе заменено. Рекомендую через 50 т.км повторить. Пыльник ШРУСа наружного левого порван — клиент отложил.", "",
       # cvt_fluid is done in this visit: the regular-interval rule covers it, not the LLM
       [("cv_boot", "declined", None, None)]),
    ex("Kia", "Sportage", 2019, 85000, ["Заправка кондиционера"], "",
       "Салонный фильтр сильно загрязнён, заменить при следующем ТО. Щётки стеклоочистителя оставляют разводы.",
       [("cabin_filter", "future", None, None), ("wiper_blades", "future", None, None)]),
    ex("Ford", "Focus", 2012, 230000, ["Замена сцепления"],
       "Сцепление заменено. Опора двигателя правая треснула, рекомендована замена. АКБ слабый, 2018 г.в.", "",
       [("engine_mounts", "future", None, None), ("battery", "future", None, None)]),
    ex("Mitsubishi", "Outlander", 2016, 140000, ["Замена масла ДВС", "Замена свечей зажигания"], "",
       "Свечи заменены. Рекомендуется замена масла в АКПП (не менялось с покупки).",
       [("atf_auto", "future", None, None)]),
    ex("Chevrolet", "Niva", 2013, 160000, ["Замена масла в раздаточной коробке"],
       "Подшипник ступицы передний правый гудит, замена через 1-2 т.км. Рулевые наконечники в норме.", "",
       [("wheel_bearing_front", "future", 1000, None)]),
    ex("Lada", "Largus", 2018, 95000, ["Компьютерная диагностика"],
       "Ошибка по датчику кислорода, рекомендована замена лямбда-зонда. Клиент отказался по цене.", "",
       [("oxygen_sensor", "declined", None, None)]),
    ex("Toyota", "RAV4", 2021, 40000, ["ТО-3"], "",
       "Следующее ТО через 10 000 км или через 12 месяцев.", [("maintenance_service", "future", 10000, 12)]),
    ex("BMW", "X3", 2015, 155000, ["Замена передних тормозных дисков и колодок"],
       "Задние диски на износе, замена через 5 т.км. Развал-схождение после замены рычага обязательно.", "",
       [("brake_discs_rear", "future", 5000, None), ("wheel_alignment", "future", 0, None)]),
    ex("Hyundai", "Creta", 2020, 70000, ["Замена масла ДВС"],
       "Мастер Сидоров А.В. Подтекает амортизатор задний левый — замена. Клиент: «сделаю в следующий раз».", "",
       [("shock_absorbers_rear", "declined", None, None)]),
    ex("Volkswagen", "Tiguan", 2014, 190000, ["Замена цепи ГРМ"], "Цепь ГРМ заменена, растяжение было критическим.", "", []),
    ex("Lada", "XRAY", 2017, 120000, ["Замена ремня ГРМ"],
       "Свечи зажигания — замена через 10 т.км. Тормозную жидкость менять через 1 год.", "",
       [("spark_plugs", "future", 10000, None), ("brake_fluid", "future", None, 12)]),
    ex("Kia", "Ceed", 2016, 135000, ["Замена ремня приводного навесного оборудования"],
       "Клиент интересовался стоимостью покраски бампера.", "", []),
]  # fmt: skip


def main() -> None:
    path = Path(__file__).parent / "extraction.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for item in EXAMPLES:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"{len(EXAMPLES)} examples -> {path}")


if __name__ == "__main__":
    main()
